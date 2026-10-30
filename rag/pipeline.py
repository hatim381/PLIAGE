"""Orchestration multi-RAG.

1. route (familles + fichier ciblé)          — Python
2. recherches ciblées                         — Premium, dans la conversation
3. collecte, filtrage fichier, déduplication  — Python
4. relance des familles sans passage (≤ 3)    — Premium
5. extraction des candidats                   — LLM sans outils, JSON
6. ancrage, consolidation                     — Python
7. indicateurs encore manquants : recherches dédiées par indicateur,
   puis nouvelle extraction sur ces seuls indicateurs
8. validation                                 — Python
"""

import logging
import time

from pliage.config import EXTRACTION_MODEL
from pliage.errors import ExtractionParseError
from pliage.models import PREMIUM_ID
from pliage.parser import (
    extract_knowledge_results, extract_sources, extract_tool_calls, is_knowledge_tool,
)
from rag.catalog import FAMILIES_BY_KEY, indicators_for
from rag.consolidator import consolidate
from rag.extractor import build_extraction_messages, parse_extraction_response, to_candidates
from rag.retrieval import (
    MAX_ATTEMPTS, EvidenceStore, build_indicator_queries, build_queries, build_retrieval_prompt,
)
from rag.schemas import CONFIRMED, NEEDS_VERIFICATION, NOT_FOUND, ExtractionReport
from rag.validator import validate

log = logging.getLogger("rag.pipeline")

PREMIUM_NAME = "[PLIAGE] Le Premium"
RETRIEVAL_TIMEOUT = 360


def _family_labels(keys):
    return ", ".join(FAMILIES_BY_KEY[k].label for k in keys)


def _search_turn(client, conv, plan, queries, attempt, store, report):
    """Un tour Premium : exécute les requêtes et ajoute les passages au store."""
    report.queries.extend(queries)
    turn = client.run_openwebui_turn(
        build_retrieval_prompt(queries, plan), conv, PREMIUM_ID, PREMIUM_NAME,
        timeout=RETRIEVAL_TIMEOUT,
    )
    calls = [c for c in extract_tool_calls(turn.content) if is_knowledge_tool(c.name)]
    results = extract_knowledge_results(turn.message)
    added = store.add_results(results, queries)
    log.info(
        "attempt=%d : %d appels outil, %d passages bruts, %d nouveaux",
        attempt, len(calls), len(results), added,
    )
    report.debug.setdefault("retrieval", []).append({
        "attempt": attempt,
        "chat_id": turn.chat_id,
        "task_ids": turn.task_ids,
        "assistant_message_id": turn.assistant_message_id,
        "message_match": turn.match_method,
        "model": turn.model,
        "elapsed_s": round(turn.elapsed, 1),
        "planned_queries": [q.text for q in queries],
        "executed_queries": [c.query for c in calls],
        "raw_chunks": len(results),
        "new_chunks": added,
    })
    if not calls:
        report.warnings.append(
            f"Tentative {attempt} : le Premium n'a appelé aucun outil de recherche documentaire."
        )
    return added


def _retrieve(client, conv, plan, report, store, on_step):
    pending = list(plan.families)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        queries = build_queries(plan, pending, attempt)
        if not queries:
            break
        if attempt == 1:
            on_step("📚 Recherches ciblées envoyées au Premium : " + _family_labels(pending) + "…")
        else:
            on_step(f"🔁 Reformulation (tentative {attempt}) : {_family_labels(pending)}…")
        _search_turn(client, conv, plan, queries, attempt, store, report)
        pending = store.missing_families(pending)
        if not pending:
            break
    return pending


def _targeted_pass(client, conv, plan, report, store, indicators, on_step):
    """Indicateurs requis non confirmés : recherches dédiées, puis nouvelle extraction.

    Les gros passages contiennent les mots-clés de toutes les familles : une
    famille paraît couverte alors que la page utile n'a jamais été ramenée.
    """
    missing = [
        ind for ind in indicators
        if not ind.optional and ind.queries
        and report.results[ind.key].status in (NOT_FOUND, NEEDS_VERIFICATION)
    ]
    queries = build_indicator_queries(plan, missing, MAX_ATTEMPTS + 1)
    if not queries:
        return
    on_step("🎯 Recherches dédiées : " + ", ".join(ind.label for ind in missing) + "…")
    before = len(store.items)
    _search_turn(client, conv, plan, queries, MAX_ATTEMPTS + 1, store, report)
    if not store.items:
        return
    # Nouveaux passages en tête : le plafond de preuves ne doit pas les couper.
    # Même sans nouveau passage, une extraction limitée à ces indicateurs
    # retrouve des valeurs noyées dans une première extraction trop large.
    evidence = store.items[before:] + store.items[:before]
    on_step(f"🧠 Extraction dédiée ({len(store.items) - before} passage(s) supplémentaire(s))…")
    raw = _extract(client, plan, evidence, missing, report)
    report.candidates.extend(to_candidates(raw, store.items))
    report.results = consolidate(report.candidates, indicators)


def _extract(client, plan, evidence, indicators, report):
    messages = build_extraction_messages(plan, evidence, indicators)
    text = client.call_standard(messages, EXTRACTION_MODEL, timeout=300)
    try:
        return parse_extraction_response(text)
    except ExtractionParseError as error:
        log.warning("extraction JSON illisible, nouvel essai : %s", error)
        report.warnings.append("Réponse d'extraction illisible au premier essai : nouvel essai effectué.")
        messages = messages + [
            {"role": "assistant", "content": text[:4000]},
            {"role": "user", "content": "Réponse invalide. Renvoie uniquement l'objet JSON demandé."},
        ]
        return parse_extraction_response(client.call_standard(messages, EXTRACTION_MODEL, timeout=300))


def _used_sources(report):
    cited = {r.chunk_id for r in report.results.values() if r.chunk_id}
    used = [e for e in report.evidence if e.chunk_id in cited] or report.evidence
    return extract_sources(used)


def _add_scope_warnings(plan, report, store):
    if store.discarded_other_file:
        report.warnings.append(
            f"{store.discarded_other_file} passage(s) d'autres documents écarté(s) "
            f"(fichier demandé : {plan.target_file})."
        )
    if store.discarded_no_source:
        report.warnings.append(f"{store.discarded_no_source} passage(s) sans nom de fichier écarté(s).")
    if not plan.target_file:
        names = {e.source for e in store.items if e.source}
        if len(names) > 1:
            report.warnings.append(
                f"Aucun fichier précisé : passages issus de {len(names)} documents. "
                "Précisez le fichier pour éviter tout mélange entre établissements."
            )


def run_multi_rag(client, conv, plan, on_step=lambda label: None, visual_review=None):
    started = time.time()
    report = ExtractionReport(plan=plan)
    indicators = indicators_for(plan.families)
    store = EvidenceStore(plan)

    on_step(f"🔎 Analyse de la demande : {len(plan.families)} famille(s) — {_family_labels(plan.families)}")
    missing = _retrieve(client, conv, plan, report, store, on_step)

    on_step(f"🧮 Filtrage et déduplication : {len(store.items)} passage(s) retenu(s)")
    report.evidence = store.items
    report.discarded_evidence = store.discarded_other_file + store.discarded_no_source
    _add_scope_warnings(plan, report, store)
    if missing:
        report.warnings.append(f"Aucun passage pertinent pour : {_family_labels(missing)}.")

    if store.items:
        on_step("🧠 Extraction des valeurs depuis les passages…")
        raw = _extract(client, plan, store.items, indicators, report)
        report.candidates = to_candidates(raw, store.items)
    else:
        report.warnings.append("Aucun passage documentaire exploitable : extraction non lancée.")
    report.results = consolidate(report.candidates, indicators)

    _targeted_pass(client, conv, plan, report, store, indicators, on_step)
    report.evidence = store.items

    if visual_review:
        from rag.visual_review import review_pages, apply_visual_review
        try:
            visual_evidence, visual_candidates = review_pages(client, plan, indicators, visual_review, on_step)
            apply_visual_review(report, visual_evidence, visual_candidates, indicators)
        except Exception as error:
            log.warning("Relecture visuelle interrompue (%s)", type(error).__name__)
            report.warnings.append("Relecture visuelle indisponible : vérifie le fichier, les pages et le modèle compatible images. Résultats OCR conservés.")

    on_step("🛡️ Validation des valeurs…")
    report.checks = validate(report.results, report.evidence)
    report.sources = _used_sources(report)

    statuses = [r.status for r in report.results.values()]
    report.debug.update({
        "elapsed_s": round(time.time() - started, 1),
        "chat_id": conv.chat_id,
        "parent_id": conv.parent_id,
        "extraction_model": EXTRACTION_MODEL,
        "families": plan.families,
        "target_file": plan.target_file,
        "evidence_count": len(store.items),
        "discarded": {
            "other_file": store.discarded_other_file,
            "no_source": store.discarded_no_source,
            "other_sources": sorted(store.other_sources)[:10],
        },
        "candidates": len(report.candidates),
        "confirmed": statuses.count(CONFIRMED),
        "not_found": statuses.count(NOT_FOUND),
        "validation": [{"check": c.name, "ok": c.ok, "message": c.message} for c in report.checks],
    })
    log.info(
        "multi-RAG terminé en %.1fs : %d confirmés / %d",
        time.time() - started, statuses.count(CONFIRMED), len(statuses),
    )
    return report
