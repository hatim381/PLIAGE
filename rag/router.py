"""Routeur léger : familles d'indicateurs nécessaires + fichier ciblé."""

import logging
import re

from rag.catalog import FAMILIES
from rag.schemas import RoutePlan
from rag.text import normalize

log = logging.getLogger("rag.router")

_EXTENSIONS = r"pdf|docx?|xlsx?|odt|ods|txt|csv|pptx?"

_QUOTED_FILE_RE = re.compile(
    rf"[\"«“]\s*([^\"«»“”\n]+?\.(?:{_EXTENSIONS}))\s*[\"»”]", re.IGNORECASE
)
_FILE_RE = re.compile(
    rf"([^\n\"«»“”:;,()\[\]]+?\.(?:{_EXTENSIONS}))\b", re.IGNORECASE
)
# Mots introduisant le nom de fichier ; on garde ce qui suit le dernier.
_LEAD_IN_RE = re.compile(
    r"^.*\b(?:dans|depuis|selon|d'apr[eè]s|d’apr[eè]s|fichier|document)\s+"
    r"(?:le\s+|la\s+|l'|l’)?",
    re.IGNORECASE,
)

_ALL_INDICATORS_RE = re.compile(
    r"tous les indicateurs|toutes les (?:donnees|variables|informations)|fiche (?:complete|aci)",
)


def detect_target_file(question):
    if not question:
        return None
    match = _QUOTED_FILE_RE.search(question)
    if match:
        return match.group(1).strip()
    match = _FILE_RE.search(question)
    if not match:
        return None
    candidate = match.group(1).strip()
    # « Dans MAISON X.pdf » -> « MAISON X.pdf » (sans toucher « DE » / « DU » du nom).
    stripped = _LEAD_IN_RE.sub("", candidate).strip()
    return stripped or candidate


def target_label(target_file):
    if not target_file:
        return None
    return re.sub(rf"\.(?:{_EXTENSIONS})$", "", target_file, flags=re.IGNORECASE).strip()


def detect_years(question):
    return sorted(set(re.findall(r"\b(20\d{2})\b", question or "")))


def detect_families(question):
    text = normalize(question)
    if _ALL_INDICATORS_RE.search(text):
        return [family.key for family in FAMILIES]
    return [
        family.key for family in FAMILIES
        if any(re.search(trigger, text) for trigger in family.triggers)
    ]


def route(question):
    target = detect_target_file(question)
    plan = RoutePlan(
        question=question,
        families=detect_families(question),
        target_file=target,
        target_label=target_label(target),
        years=detect_years(question),
    )
    log.info(
        "%d familles détectées %s ; fichier ciblé=%r",
        len(plan.families), plan.families, plan.target_file,
    )
    return plan
