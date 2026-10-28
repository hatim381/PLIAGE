"""Recherches ciblées : requêtes, prompt de collecte, filtrage des passages.

Pliage n'expose pas `query_knowledge_files` en appel direct : c'est le
Premium qui exécute les recherches (une par requête). On récupère ensuite
les résultats bruts des tool calls, sans passer par sa synthèse.
"""

import logging
import re

from rag.catalog import FAMILIES_BY_KEY
from rag.schemas import Evidence, RAGQuery
from rag.text import normalize

log = logging.getLogger("rag.search")

MAX_ATTEMPTS = 3
RETRIEVAL_DONE_MARKER = "RECHERCHES TERMINÉES"


def build_queries(plan, families, attempt):
    """Requêtes de la tentative `attempt` (1-based) pour les familles données."""
    target = plan.target_label or ""
    years = " ".join(plan.years)
    queries = []
    for key in families:
        family = FAMILIES_BY_KEY[key]
        if attempt > len(family.queries):
            continue
        text = family.queries[attempt - 1].format(target=target, years=years)
        queries.append(RAGQuery(family=key, text=re.sub(r"\s+", " ", text).strip(), attempt=attempt))
    return queries


def build_retrieval_prompt(queries, plan):
    lines = [
        "TÂCHE TECHNIQUE — COLLECTE DE PASSAGES DOCUMENTAIRES",
        "",
        "Ne réponds pas à la question : collecte uniquement des passages.",
        "Pour CHACUNE des requêtes ci-dessous, appelle l'outil "
        "`query_knowledge_files` (un appel par requête), avec la requête "
        "copiée mot pour mot dans le paramètre `query`.",
    ]
    if plan.target_file:
        lines.append(
            f"Le document ciblé est « {plan.target_file} ». Si l'outil permet "
            "de restreindre la recherche à ce fichier, fais-le."
        )
    lines += ["", "Requêtes :"]
    lines += [f"{i}. {q.text}" for i, q in enumerate(queries, start=1)]
    lines += [
        "",
        "Règles :",
        "- n'invente aucune valeur et ne résume pas les résultats ;",
        "- n'utilise que les outils de recherche documentaire ;",
        f"- quand toutes les requêtes sont exécutées, réponds uniquement : {RETRIEVAL_DONE_MARKER}",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------
# Fichier ciblé
# ------------------------------------------------------------

def _file_key(name):
    name = re.sub(r"\.[a-z0-9]{2,4}$", "", normalize(name))
    return re.sub(r"[\s_\-.]+", " ", name).strip()


def source_matches_target(source, target):
    if not target:
        return True
    if not source:
        return False
    a, b = _file_key(source), _file_key(target)
    if a == b:
        return True
    shorter = min(a, b, key=len)
    return len(shorter) >= 8 and (a in b or b in a)


# ------------------------------------------------------------
# Passages -> preuves
# ------------------------------------------------------------

def _tokens(text):
    return set(re.findall(r"[a-z0-9]{3,}", normalize(text)))


def query_family(query, planned):
    """Famille de la requête planifiée la plus proche de celle exécutée."""
    if not query:
        return None
    executed = _tokens(query)
    best, best_score = None, 0.0
    for planned_query in planned:
        expected = _tokens(planned_query.text)
        if not expected:
            continue
        score = len(executed & expected) / len(expected)
        if score > best_score:
            best, best_score = planned_query.family, score
    return best if best_score >= 0.6 else None


def keyword_families(content, families):
    text = normalize(content)
    return [
        key for key in families
        if any(re.search(rf"\b{re.escape(normalize(w))}", text) for w in FAMILIES_BY_KEY[key].keywords)
    ]


def _content_key(result):
    return ((result.source or "").lower(), re.sub(r"\s+", " ", result.content).strip().lower())


class EvidenceStore:
    """Accumule les preuves des différentes tentatives, sans doublon."""

    def __init__(self, plan):
        self.plan = plan
        self.items = []
        self.discarded_other_file = 0
        self.discarded_no_source = 0
        self.other_sources = set()
        self._index = {}

    def _accepts(self, result):
        if not self.plan.target_file:
            return True
        if not result.source:
            self.discarded_no_source += 1
            return False
        if not source_matches_target(result.source, self.plan.target_file):
            self.discarded_other_file += 1
            self.other_sources.add(result.source)
            return False
        return True

    def add_results(self, results, planned):
        added = 0
        for result in results:
            if not self._accepts(result):
                continue

            queries = [q for q in (result.query or "").split(" || ") if q]
            searched_by = {f for f in (query_family(q, planned) for q in queries) if f}
            families = set(keyword_families(result.content, self.plan.families)) | searched_by

            key = _content_key(result)
            existing = self._index.get(key)
            if existing:
                existing.queries.extend(q for q in queries if q not in existing.queries)
                existing.families = sorted(set(existing.families) | families)
                existing.searched_by = sorted(set(existing.searched_by) | searched_by)
                continue

            evidence = Evidence(
                chunk_id=f"C{len(self.items) + 1}",
                content=result.content,
                source=result.source,
                file_id=result.file_id,
                distance=result.distance,
                page=result.page,
                queries=queries,
                families=sorted(families),
                searched_by=sorted(searched_by),
            )
            self._index[key] = evidence
            self.items.append(evidence)
            added += 1
        log.info(
            "%d passages conservés (+%d), %d écartés (autre fichier), %d sans source",
            len(self.items), added, self.discarded_other_file, self.discarded_no_source,
        )
        return added

    def missing_families(self, families):
        """Familles dont aucune requête n'a ramené de passage pertinent du fichier ciblé.

        Un passage ramené par la requête d'une autre famille ne compte pas :
        les gros passages contiennent souvent tous les mots-clés.
        """
        covered = {
            f for item in self.items for f in item.searched_by if f in item.families
        }
        return [key for key in families if key not in covered]
