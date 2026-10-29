"""Consolidation des candidats en un résultat par indicateur.

Règles :
- seules les valeurs ancrées de type `establishment_value` peuvent être
  confirmées ; les autres (méthodologiques, maximums...) restent en trace ;
- plusieurs valeurs établissement différentes => `conflict`, sans choix ;
- aucune valeur établissement => `not_found` (jamais 0 par défaut).
"""

import logging

from rag.schemas import (
    CONFIRMED, CONFLICT, NEEDS_VERIFICATION, NOT_FOUND, UNKNOWN, IndicatorResult,
)
from rag.text import format_number, normalize

log = logging.getLogger("rag.consolidator")


def same_value(a, b, kind):
    if kind == "text":
        na, nb = normalize(a), normalize(b)
        return na == nb or na in nb or nb in na
    tolerance = 0.01 if kind == "money" else 1e-6
    return abs(float(a) - float(b)) <= tolerance


def group_by_value(candidates, kind):
    groups = []
    for candidate in candidates:
        for group in groups:
            if same_value(group[0].value, candidate.value, kind):
                group.append(candidate)
                break
        else:
            groups.append([candidate])
    return groups


def _digits(value):
    return f"{float(value):.2f}".replace(".", "").lstrip("0")


def _drop_ocr_prefix_variants(groups, kind):
    """Montants dont l'un n'est que l'autre précédé d'un chiffre parasite (OCR).

    « s'élève 412 345,67 € » lu pour « à 12 345,67 € » : le montant le plus
    court est retenu seulement s'il est lui-même attesté par un autre groupe.
    """
    if kind != "money" or len(groups) < 2:
        return groups
    kept = []
    for group in groups:
        digits = _digits(group[0].value)
        is_variant = any(
            other is not group
            and len(_digits(other[0].value)) == len(digits) - 1
            and digits.endswith(_digits(other[0].value))
            for other in groups
        )
        if is_variant:
            log.info("montant %s écarté : variante OCR d'un autre montant", group[0].value)
        else:
            kept.append(group)
    return kept


def display_value(value, kind):
    if value is None:
        return ""
    return str(value) if kind == "text" else format_number(value, kind)


def _confirm(result, group):
    best = max(group, key=lambda c: len(c.quote or ""))
    result.status = CONFIRMED
    result.value = best.value
    result.unit = best.unit or result.unit
    result.value_type = best.value_type
    result.source = best.source
    result.file_id = best.file_id
    result.evidence = best.quote
    result.chunk_id = best.chunk_id
    result.query = best.query
    periods = sorted({c.period for c in group if c.period})
    result.period = ", ".join(periods) if periods else None


def _set_fallback_status(result, candidates, kind):
    grounded = [c for c in candidates if c.grounded and c.rejection is None]
    unknown = [c for c in grounded if c.value_type == UNKNOWN]
    if unknown:
        result.status = NEEDS_VERIFICATION
        result.value = unknown[0].value
        result.source = unknown[0].source
        result.evidence = unknown[0].quote
        result.chunk_id = unknown[0].chunk_id
        result.messages.append(
            unknown[0].note or "Type de valeur non déterminé (établissement ou référence ?)."
        )
        return

    rejected = [c for c in candidates if c.rejection]
    if rejected and not grounded:
        result.status = NEEDS_VERIFICATION
        values = ", ".join(display_value(c.value, kind) or "?" for c in rejected)
        result.messages.append(
            f"Valeur proposée non retrouvée dans les passages ({values}) : non retenue."
        )
        return

    result.status = NOT_FOUND
    if grounded:
        traces = ", ".join(f"{display_value(c.value, kind)} ({c.value_type})" for c in grounded)
        result.messages.append(f"Seules des valeurs non établissement trouvées : {traces}.")


def consolidate_indicator(ind, candidates):
    result = IndicatorResult(
        indicator=ind.key,
        label=ind.label,
        unit=ind.unit,
        optional=ind.optional,
        candidates=list(candidates),
    )
    usable = [c for c in candidates if c.usable]
    if not usable:
        _set_fallback_status(result, candidates, ind.kind)
        return result

    groups = _drop_ocr_prefix_variants(group_by_value(usable, ind.kind), ind.kind)
    if len(groups) > 1:
        result.status = CONFLICT
        values = " / ".join(display_value(g[0].value, ind.kind) for g in groups)
        result.messages.append(f"Valeurs établissement contradictoires : {values}.")
        return result

    _confirm(result, groups[0])
    for c in candidates:
        if c.grounded and not c.usable and not same_value(c.value, result.value, ind.kind):
            result.messages.append(
                f"Valeur {display_value(c.value, ind.kind)} écartée ({c.value_type})."
            )
    return result


def consolidate(candidates, indicators):
    results = {}
    for ind in indicators:
        own = [c for c in candidates if c.indicator == ind.key]
        result = consolidate_indicator(ind, own)
        results[ind.key] = result
        level = logging.WARNING if result.status != CONFIRMED else logging.INFO
        log.log(level, "%s %s", ind.key, result.status)
    return results
