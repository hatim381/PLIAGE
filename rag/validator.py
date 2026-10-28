"""Validation Python des valeurs consolidées.

Un contrôle échoué ne supprime jamais une valeur : il la passe en
`needs_verification` avec la raison, pour vérification humaine.
"""

import logging

from rag.catalog import INDICATORS_BY_KEY
from rag.schemas import CONFIRMED, ValidationCheck
from rag.text import format_number

log = logging.getLogger("rag.validation")

MONEY_TOLERANCE = 1.0  # euros : arrondis des tableaux ACI
RATE_TOLERANCE_POINTS = 1.5  # points de % : taux arrondi à l'unité + bases différentes


def _confirmed(results, key):
    result = results.get(key)
    return result if result and result.status == CONFIRMED and result.value is not None else None


def _record(checks, results, name, ok, message, keys):
    checks.append(ValidationCheck(name=name, ok=ok, message=message, indicators=tuple(keys)))
    if ok:
        return
    log.warning("%s : %s", name, message)
    for key in keys:
        if key in results:
            results[key].flag(message)


def check_value_ranges(results, checks):
    for key, result in results.items():
        if result.status != CONFIRMED or result.value is None:
            continue
        kind = INDICATORS_BY_KEY[key].kind
        value = result.value
        if kind == "text":
            continue
        if value < 0:
            _record(checks, results, "valeur_positive", False,
                    f"{result.label} : valeur négative impossible ({value}).", [key])
        elif kind == "count" and not float(value).is_integer():
            _record(checks, results, "entier", False,
                    f"{result.label} : un effectif doit être entier ({value}).", [key])
        elif kind == "percent" and not 0 <= value <= 100:
            _record(checks, results, "pourcentage", False,
                    f"{result.label} : pourcentage impossible ({format_number(value, 'percent')}).", [key])


def check_rate_coherence(results, checks, prefix):
    patients = _confirmed(results, f"{prefix}_patients")
    rate = _confirmed(results, f"{prefix}_taux")
    bases = [
        b for b in (_confirmed(results, "patientele_reference"), _confirmed(results, "file_active"))
        if b and b.value
    ]
    if not (patients and rate and bases) or not 0 <= rate.value <= 100:
        return
    computed = [patients.value / base.value * 100 for base in bases]
    ok = any(abs(c - rate.value) <= RATE_TOLERANCE_POINTS for c in computed)
    detail = " ou ".join(f"{c:.1f} %" for c in computed)
    message = (
        f"{prefix.upper()} : {format_number(patients.value, 'count')} patients "
        f"≈ {detail} de la patientèle ; taux indiqué {format_number(rate.value, 'percent')}."
    )
    if not ok:
        message += " Incohérence à vérifier."
    _record(checks, results, f"coherence_{prefix}", ok, message, [f"{prefix}_patients", f"{prefix}_taux"])


def check_aci_sum(results, checks):
    total = _confirmed(results, "aci_remuneration_totale")
    advance = _confirmed(results, "aci_avance_exercice")
    balance = _confirmed(results, "aci_solde_exercice")
    keys = ["aci_remuneration_totale", "aci_avance_exercice", "aci_solde_exercice"]

    if total and advance and not balance and abs(total.value - advance.value) <= 0.01:
        _record(checks, results, "aci_avance_vs_total", False,
                "Avance égale à la rémunération totale : possible confusion avance / total.",
                keys[:2])
        return
    if not (total and advance and balance):
        return
    computed = advance.value + balance.value
    ok = abs(computed - total.value) <= MONEY_TOLERANCE
    message = (
        f"Avance {format_number(advance.value, 'money')} + solde {format_number(balance.value, 'money')} "
        f"= {format_number(computed, 'money')} ; total indiqué {format_number(total.value, 'money')}."
    )
    _record(checks, results, "aci_avance_plus_solde", ok, message, keys)


def check_professionals(results, checks, group):
    total = _confirmed(results, f"{group}_total")
    if not total:
        return
    parts = [
        r for k, r in results.items()
        if k.startswith(f"{group}_") and k != f"{group}_total" and r.status == CONFIRMED
    ]
    if not parts:
        return
    subtotal = sum(r.value for r in parts)
    ok = subtotal <= total.value
    message = (
        f"{group.capitalize()} : somme des catégories {format_number(subtotal, 'count')} "
        f"pour un total de {format_number(total.value, 'count')}."
    )
    keys = [f"{group}_total"] + [p.indicator for p in parts]
    _record(checks, results, f"total_{group}", ok, message, keys)


def check_protocols(results, checks):
    done = _confirmed(results, "protocoles_realises")
    paid = _confirmed(results, "protocoles_remuneres")
    if not (done and paid):
        return
    _record(
        checks, results, "protocoles", paid.value <= done.value,
        f"Protocoles rémunérés ({format_number(paid.value, 'count')}) "
        f"vs réalisés ({format_number(done.value, 'count')}).",
        ["protocoles_realises", "protocoles_remuneres"],
    )


def validate(results):
    checks = []
    check_value_ranges(results, checks)
    check_rate_coherence(results, checks, "c2s")
    check_rate_coherence(results, checks, "ame")
    check_aci_sum(results, checks)
    check_professionals(results, checks, "associes")
    check_professionals(results, checks, "vacataires")
    check_protocols(results, checks)
    return checks
