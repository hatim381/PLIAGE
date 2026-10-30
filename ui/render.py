"""Rendu de la réponse : tableau, sources, preuves, debug.

`build_answer_markdown` est pur (testable) ; les fonctions `render_*`
utilisent Streamlit.
"""

import json

from rag.catalog import INDICATORS_BY_KEY
from rag.consolidator import display_value
from rag.schemas import CONFIRMED, CONFLICT, NEEDS_VERIFICATION, NOT_FOUND

STATUS_BADGES = {
    CONFIRMED: "✅ Confirmé",
    NOT_FOUND: "⚪ Non trouvé",
    NEEDS_VERIFICATION: "⚠️ À vérifier",
    CONFLICT: "🔴 Conflit",
}

DEBUG_TEXT_LIMIT = 400


def _cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def _value_cell(result):
    kind = INDICATORS_BY_KEY[result.indicator].kind
    if result.status == CONFLICT:
        return "Valeurs divergentes"
    if result.value is None:
        return "Non trouvé" if result.status == NOT_FOUND else "Non confirmé"
    text = display_value(result.value, kind)
    if result.period:
        text += f" ({result.period})"
    return text


def visible_results(report):
    return [r for r in report.results.values() if not (r.optional and r.status == NOT_FOUND)]


def build_answer_markdown(report):
    rows = visible_results(report)
    if not rows:
        return "Aucun indicateur à extraire n'a été identifié dans la demande."

    lines = [
        "| Indicateur | Valeur | Statut | Source |",
        "|---|---:|---|---|",
    ]
    for result in rows:
        lines.append(
            f"| {_cell(result.label)} | {_cell(_value_cell(result))} "
            f"| {STATUS_BADGES[result.status]} | {_cell(result.source or '—')} |"
        )

    if report.sources:
        lines += ["", "### Sources utilisées", ""]
        lines += [f"- 📄 **{_cell(source.name)}**" for source in report.sources]

    to_check = [
        f"- **{r.label}** : " + " ".join(r.messages)
        for r in rows if r.status in (CONFLICT, NEEDS_VERIFICATION) and r.messages
    ]
    to_check += [f"- {warning}" for warning in report.warnings]
    if to_check:
        lines += ["", "### Points à vérifier", ""] + to_check

    if report.checks:
        passed = sum(1 for c in report.checks if c.ok)
        lines += ["", f"🛡️ Contrôles de cohérence : {passed}/{len(report.checks)} validés."]
        lines.append("Ces contrôles ne certifient pas la fidélité de la lecture du document.")
    return "\n".join(lines)


# ------------------------------------------------------------
# Streamlit
# ------------------------------------------------------------

def _render_result_evidence(st, result, evidence_by_id):
    kind = INDICATORS_BY_KEY[result.indicator].kind
    st.markdown(f"**Valeur :** {_value_cell(result)}  \n**Type :** `{result.value_type or '—'}`")
    if result.source:
        st.markdown(f"**Source :** {result.source}")
    if result.query:
        st.markdown(f"**Requête RAG :** `{result.query}`")
    if result.evidence:
        st.markdown("**Passage cité :**")
        st.code(result.evidence, language="text", wrap_lines=True)
    for message in result.messages:
        st.warning(message)

    if result.candidates:
        st.markdown("**Candidats examinés :**")
        for c in result.candidates:
            shown = display_value(c.value, kind) if c.value not in (None, "") else "?"
            state = "✅ retenu" if c.usable else f"✖️ {c.rejection or 'écarté'}"
            st.markdown(f"- `{shown}` — {c.value_type} — {state} — {c.chunk_id or '?'}")

    chunk = evidence_by_id.get(result.chunk_id)
    if chunk:
        meta = [f"passage {chunk.chunk_id}"]
        if chunk.page is not None:
            meta.append(f"page (métadonnée Pliage) : {chunk.page}")
        if chunk.distance is not None:
            meta.append(f"distance : {chunk.distance:.4f}")
        st.caption(" · ".join(meta))


def render_evidence(report):
    import streamlit as st

    evidence_by_id = {e.chunk_id: e for e in report.evidence}
    st.markdown("#### 🔎 Preuves")
    for result in visible_results(report):
        if result.status == NOT_FOUND and not result.candidates:
            continue
        with st.expander(f"🔎 {result.label} — preuve ({STATUS_BADGES[result.status]})"):
            _render_result_evidence(st, result, evidence_by_id)

    if report.evidence:
        with st.expander(f"📚 Tous les passages retenus ({len(report.evidence)})"):
            for e in report.evidence:
                families = ", ".join(e.families) or "—"
                st.markdown(f"**{e.chunk_id}** — {e.source or 'source inconnue'} — familles : {families}")
                st.code(e.content, language="text", wrap_lines=True)


def _truncate(value, limit=DEBUG_TEXT_LIMIT):
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"… (+{len(value) - limit} car.)"
    if isinstance(value, (list, tuple)):
        return [_truncate(v, limit) for v in value[:50]]
    if isinstance(value, dict):
        return {k: _truncate(v, limit) for k, v in value.items()}
    return value


def render_debug(debug):
    import streamlit as st

    st.divider()
    st.subheader("🛠️ Debug Pliage")
    safe = json.loads(json.dumps(_truncate(debug), default=str, ensure_ascii=False))
    st.json(safe, expanded=False)


def diagnostic_entries(report):
    """Exact received passages and candidate decisions, including rejected ones."""
    entries = []
    for result in visible_results(report):
        if not (result.indicator.startswith(("associes_", "vacataires_", "protocoles_"))
                or result.indicator == "presence_ipa"):
            continue
        ids = {c.chunk_id for c in result.candidates if c.chunk_id}
        if result.chunk_id:
            ids.add(result.chunk_id)
        chunks = [e for e in report.evidence if e.chunk_id in ids]
        if not chunks:
            family = INDICATORS_BY_KEY[result.indicator].family
            chunks = [e for e in report.evidence if family in e.families]
        entries.append({
            "indicateur": result.indicator,
            "statut": result.status,
            "valeur": result.value,
            "messages": result.messages,
            "candidats": [{
                "valeur": c.value, "citation": c.quote, "passage": c.chunk_id,
                "type": c.value_type, "ancre": c.grounded,
                "rejet": c.rejection, "motif": c.note,
            } for c in result.candidates],
            "passages_recus": [{
                "id": e.chunk_id, "source": e.source, "page": e.page,
                "texte_integral": e.content,
            } for e in chunks],
        })
    return entries


def render_diagnostics(report):
    import streamlit as st

    entries = diagnostic_entries(report)
    if entries:
        with st.expander("Diagnostic des effectifs, protocoles et IPA"):
            st.caption(
                "Texte exact reçu de Pliage, citations et décisions d'extraction. "
                "Une page absente des métadonnées reste inconnue. "
                "Les passages longs peuvent avoir été tronqués avant envoi au modèle."
            )
            st.json(entries, expanded=False)
