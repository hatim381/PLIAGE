"""Extraction structurée des candidats et ancrage dans les passages.

Le LLM (sans outils) propose des candidats {indicateur, valeur, citation,
type}. Python vérifie ensuite que la citation existe dans le passage, que
la valeur figure dans la citation, et reclasse les valeurs dont le contexte
est méthodologique (maximum, seuil, « pour une patientèle de... »).
"""

import json
import logging
import re

from pliage.errors import ExtractionParseError
from rag.catalog import INDICATORS_BY_KEY
from rag.schemas import (
    ESTABLISHMENT, MAXIMUM, METHODOLOGICAL, POINTS, REMUNERATION, THRESHOLD,
    UNKNOWN, VALUE_TYPES, Candidate,
)
from rag.text import contains_number, normalize, number_positions, parse_number

log = logging.getLogger("rag.extractor")

MIN_TEXT_QUOTE_WORDS = 4
MAX_CHUNK_CHARS = 6000
MAX_EVIDENCE_CHARS = 90000

KIND_LABELS = {
    "count": "nombre entier",
    "percent": "pourcentage (0-100)",
    "money": "montant en euros",
    "points": "nombre de points",
    "text": "texte court",
}

HEDGE_RE = re.compile(
    r"probabl|semble|on peut (?:en conclure|supposer)|suppos|estimation|vraisemblabl|sans doute"
)

# Contexte précédant une valeur, normalisé (sans accents).
CONTEXT_PATTERNS = (
    (METHODOLOGICAL, re.compile(
        r"pour une patientele|exemple|si la structure|si le |par tranche|a titre indicatif"
        r"|valeur de reference|pour \d[\d ]* patients"
    )),
    (MAXIMUM, re.compile(r"\bmax|plafon|jusqu'?a|au plus|possible|objectif")),
    (THRESHOLD, re.compile(r"seuil|minimum|au moins|au-dela|a partir de|superieur a|inferieur a")),
)

_CLAUSE_SPLIT_RE = re.compile(r"\n|\||;|\.\s")


# ------------------------------------------------------------
# Prompt
# ------------------------------------------------------------

def _indicator_lines(indicators):
    lines = []
    for ind in indicators:
        unit = f" ({ind.unit})" if ind.unit else ""
        detail = f" — {ind.description}" if ind.description else ""
        lines.append(f"- {ind.key} — {ind.label} — {KIND_LABELS[ind.kind]}{unit}{detail}")
    return "\n".join(lines)


def _evidence_block(evidence):
    parts = []
    total = 0
    for item in evidence:
        content = item.content[:MAX_CHUNK_CHARS]
        if total + len(content) > MAX_EVIDENCE_CHARS:
            log.warning("plafond de preuves atteint : %s et suivants non envoyés", item.chunk_id)
            break
        total += len(content)
        parts.append(f"[{item.chunk_id}] fichier : {item.source or 'inconnu'}\n{content}")
    return "\n\n---\n\n".join(parts)


def build_extraction_messages(plan, evidence, indicators):
    target = plan.target_file or "aucun fichier précisé"
    user = f"""Question de l'utilisateur :
{plan.question}

Document ciblé : {target}

Indicateurs à extraire (clé — libellé — type — précision) :
{_indicator_lines(indicators)}

Passages documentaires (SEULE source autorisée) :

{_evidence_block(evidence)}

Règles :
1. Utilise uniquement ces passages : aucune connaissance externe, aucun calcul, aucune déduction.
2. Pour chaque valeur, recopie mot pour mot la citation (300 caractères max) qui contient la valeur, et l'identifiant du passage (ex. "C3").
3. Liste AUSSI les valeurs qui pourraient être confondues avec un indicateur (valeur méthodologique, maximum, seuil, points, montant), avec le bon value_type : elles servent de trace.
   Exemple : « pour une patientèle de référence de 4 000 patients » => value_type "methodological_reference".
4. value_type ∈ establishment_value, methodological_reference, maximum, threshold, points, remuneration, unknown.
5. Indicateur absent des passages : ne l'inclus pas. Jamais 0 par défaut : une absence de preuve n'est pas un zéro.
   En revanche, un 0 écrit explicitement pour la structure est une valeur valide.
6. Interdit : « probablement », « semble », « on peut supposer », estimation.
7. Un montant versé ou alloué à la structure (total, avance, solde) et des points obtenus par la structure sont des establishment_value ;
   « remuneration » et « points » servent aux montants / points d'une grille qui ne correspondent pas à l'indicateur demandé.
8. Tableaux : une ligne de barème (critère, « au moins », « maxi ») n'est atteinte par la structure que si des points ou un montant non nul lui sont associés ; cite alors la ligne avec ces valeurs.
9. Ne mélange pas : associés / vacataires ; avance / solde / total ; maximum / réalisé ; points / euros.
   OCR : « à » est souvent lu « 4 » (« s'élève 412 345,67 € » pour « à 12 345,67 € ») ; si un tableau récapitulatif donne le même montant, cite le tableau.
10. period : exercice ou année si le passage l'indique explicitement, sinon null.
11. IPA : « Si la structure intègre un IPA » est une condition de barème, jamais une présence attestée.
    Cela reste vrai si l'OCR colle les mots (« Sila structure »). Une ligne IPA à zéro point ne prouve pas une absence.
12. Effectifs : cite le groupe associés ou vacataires ET la profession avec sa valeur. Une colonne ambiguë est unknown.
13. Protocoles : extrais le nombre réalisé dans la colonne de résultat, jamais un numéro de page ou un plafond du barème.
14. Nombres : value est un nombre JSON (ex. 1250, 18450.75, 12). Textes : phrase courte fidèle au passage.

Réponds uniquement avec ce JSON :
{{"candidates": [{{"indicator": "...", "value": ..., "unit": "...", "value_type": "...", "chunk": "C1", "quote": "...", "period": null}}]}}"""

    return [
        {
            "role": "system",
            "content": "Tu es un extracteur de données documentaires. "
                       "Tu réponds uniquement par du JSON valide, sans texte autour.",
        },
        {"role": "user", "content": user},
    ]


# ------------------------------------------------------------
# Lecture de la réponse
# ------------------------------------------------------------

def parse_extraction_response(text):
    if not text or not text.strip():
        raise ExtractionParseError("Réponse d'extraction vide.")
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ExtractionParseError("Aucun objet JSON dans la réponse d'extraction.")
    try:
        data = json.loads(cleaned[start:end + 1])
    except ValueError as error:
        raise ExtractionParseError(f"JSON d'extraction invalide : {error}") from error
    candidates = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(candidates, list):
        raise ExtractionParseError("Clé « candidates » absente de la réponse d'extraction.")
    return [c for c in candidates if isinstance(c, dict)]


# ------------------------------------------------------------
# Ancrage et classification
# ------------------------------------------------------------

def _clean_for_match(text):
    return re.sub(r"[|*_#>`]+", " ", normalize(text)).split()


def quote_in_chunk(quote, chunk):
    """Citation présente dans le passage (tolère espaces, tableaux, OCR)."""
    q_tokens = _clean_for_match(quote)
    c_tokens = _clean_for_match(chunk)
    if not q_tokens:
        return False
    if " ".join(q_tokens) in " ".join(c_tokens):
        return True
    words = [t for t in q_tokens if len(t) >= 2]
    if len(words) < 3:
        return False
    chunk_set = set(c_tokens)
    return sum(1 for t in words if t in chunk_set) / len(words) >= 0.85


def _clause_before(text, position):
    parts = _CLAUSE_SPLIT_RE.split(text[:position])
    return normalize(parts[-1])


def _type_from_unit(text, end, ind):
    following = normalize(text[end:end + 14])
    if re.match(r"^(points?|pts)\b", following) and ind.kind != "points":
        return POINTS
    if re.match(r"^(€|euros?\b|eur\b)", following) and ind.kind != "money":
        return REMUNERATION
    return None


def classify_value_context(text, value, ind):
    """Type imposé par le contexte, ou None si le contexte est neutre.

    Si la valeur apparaît au moins une fois dans un contexte neutre, on
    ne force rien : le contexte ne permet pas de la disqualifier.
    """
    positions = number_positions(text, value)
    if not positions:
        return None
    forced = []
    for start, end in positions:
        kind = _type_from_unit(text, end, ind)
        if not kind:
            clause = _clause_before(text, start)
            kind = next((t for t, rx in CONTEXT_PATTERNS if rx.search(clause)), None)
        if not kind:
            return None
        forced.append(kind)
    return forced[0]


def _snippet(chunk, value):
    positions = number_positions(chunk, value)
    if not positions:
        return ""
    start, end = positions[0]
    return chunk[max(0, start - 160):end + 160].strip()


def _numeric_candidate(candidate, ind, chunk_text):
    value = parse_number(candidate.value)
    if value is None:
        candidate.rejection = "valeur non numérique"
        return candidate
    candidate.value = value

    quote_ok = bool(candidate.quote) and quote_in_chunk(candidate.quote, chunk_text)
    if quote_ok and contains_number(candidate.quote, value):
        context = candidate.quote
    elif contains_number(chunk_text, value) and (value >= 10 or not value.is_integer()):
        # Citation reformulée mais valeur bien présente : on ancre sur le passage.
        candidate.quote = _snippet(chunk_text, value)
        context = candidate.quote
    else:
        candidate.rejection = "valeur absente du passage cité"
        return candidate

    candidate.grounded = True
    if (ind.kind, candidate.value_type) in (("money", REMUNERATION), ("points", POINTS)):
        # Pour un indicateur monétaire, « remuneration » est la valeur elle-même.
        candidate.value_type = ESTABLISHMENT
    forced = classify_value_context(context, value, ind)
    if forced and candidate.value_type in (ESTABLISHMENT, UNKNOWN):
        log.info("%s=%s reclassé %s (contexte)", ind.key, value, forced)
        candidate.value_type = forced
    return candidate


def _text_candidate(candidate, chunk_text):
    value = str(candidate.value).strip() if candidate.value is not None else ""
    candidate.value = value
    if not value:
        candidate.rejection = "valeur vide"
    elif HEDGE_RE.search(normalize(value)):
        candidate.rejection = "formulation hypothétique"
    elif not candidate.quote or not quote_in_chunk(candidate.quote, chunk_text):
        candidate.rejection = "citation introuvable dans le passage"
    else:
        candidate.grounded = True
        quote = normalize(candidate.quote)
        # Inspect the clause containing an exact quote too: the model may omit
        # the leading "Si la structure" when copying a suffix of the criterion.
        local_clause = ""
        if candidate.indicator == "presence_ipa":
            for clause in re.split(r"[\n|.;]", chunk_text):
                if quote and quote in normalize(clause):
                    local_clause = normalize(clause)
                    break
        if candidate.indicator == "presence_ipa" and re.search(
            r"\bsi\s*(?:la\s*|le\s*|les\s*)?structure\b|\ben cas de\b",
            normalize(f"{candidate.value} {candidate.quote} {local_clause}"),
        ):
            candidate.value_type = METHODOLOGICAL
            candidate.note = "Condition de barème IPA, sans présence attestée dans la structure."
        if candidate.value_type == ESTABLISHMENT:
            if re.search(r"\bsi\s*(la|le|les|un|une)\b", quote):
                candidate.value_type = METHODOLOGICAL
            elif len(quote.split()) < MIN_TEXT_QUOTE_WORDS:
                # Ex. « niveau avancé » : simple libellé d'une grille, pas une assertion.
                candidate.value_type = UNKNOWN
                candidate.note = "Citation trop courte pour rattacher la valeur à la structure."
    return candidate


def _flag_ambiguous_staff_column(candidate, ind, chunk_text):
    """Associés et vacataires dans le même passage, citation sans l'un ni l'autre.

    Les tableaux scannés (OCR) mélangent souvent les deux colonnes : la
    catégorie lue peut appartenir à l'autre groupe.
    """
    if ind.family != "professionnels" or ind.key.endswith("_total"):
        return
    if not candidate.usable:
        return
    chunk = normalize(chunk_text)
    if "associ" in chunk and "vacataire" in chunk and not re.search(r"associ|vacataire", normalize(candidate.quote)):
        candidate.value_type = UNKNOWN
        candidate.note = (
            "Colonne associés / vacataires ambiguë dans le passage (tableau OCR) : "
            "à vérifier dans le document."
        )


def _reanchor(candidate, ind, evidence):
    """Valeur absente du passage cité : cherche le passage qui contient citation et valeur."""
    value = parse_number(candidate.value)
    if value is None or not candidate.quote:
        return None
    for item in evidence:
        if item.chunk_id == candidate.chunk_id:
            continue
        if contains_number(item.content, value) and quote_in_chunk(candidate.quote, item.content):
            return item
    return None


def to_candidates(raw_candidates, evidence):
    by_id = {item.chunk_id: item for item in evidence}
    candidates = []
    for raw in raw_candidates:
        ind = INDICATORS_BY_KEY.get(str(raw.get("indicator", "")).strip())
        if not ind:
            log.debug("indicateur inconnu ignoré : %r", raw.get("indicator"))
            continue
        llm_type = raw.get("value_type") if raw.get("value_type") in VALUE_TYPES else UNKNOWN
        chunk = by_id.get(str(raw.get("chunk", "")).strip())
        period = raw.get("period")
        candidate = Candidate(
            indicator=ind.key,
            value=raw.get("value"),
            unit=str(raw.get("unit") or ind.unit),
            value_type=llm_type,
            llm_value_type=llm_type,
            chunk_id=chunk.chunk_id if chunk else None,
            quote=str(raw.get("quote") or "")[:600],
            period=str(period) if period not in (None, "", "null") else None,
            source=chunk.source if chunk else None,
            file_id=chunk.file_id if chunk else None,
            query=chunk.queries[0] if chunk and chunk.queries else None,
        )
        if candidate.value is None or candidate.value == "":
            candidate.rejection = "valeur vide"
        elif not chunk:
            candidate.rejection = "passage inconnu"
        elif ind.kind == "text":
            _text_candidate(candidate, chunk.content)
        else:
            _numeric_candidate(candidate, ind, chunk.content)
            other = _reanchor(candidate, ind, evidence) if candidate.rejection else None
            if other:
                log.info("%s=%s ré-ancré de %s vers %s", ind.key, candidate.value, chunk.chunk_id, other.chunk_id)
                candidate.rejection = None
                candidate.chunk_id, candidate.source, candidate.file_id = other.chunk_id, other.source, other.file_id
                chunk = other
                _numeric_candidate(candidate, ind, chunk.content)
            _flag_ambiguous_staff_column(candidate, ind, chunk.content)
        candidates.append(candidate)
    return candidates
