"""Validation Python des valeurs consolidées.

Un contrôle échoué ne supprime jamais une valeur : il la passe en
`needs_verification` avec la raison, pour vérification humaine.
"""

import logging
import re

from rag.catalog import INDICATORS_BY_KEY
from rag.schemas import CONFIRMED, NEEDS_VERIFICATION, ValidationCheck
from rag.text import format_number, normalize

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
    # Include uncertain numerical categories: the OCR ambiguity must not hide
    # a mismatch by excluding every suspect value from the sum.
    total = _confirmed(results, f"{group}_total")
    if not total:
        return
    expected = [key for key in INDICATORS_BY_KEY
                if key.startswith(f"{group}_") and key != f"{group}_total"]
    parts = [results[key] for key in expected if key in results
             and results[key].status in (CONFIRMED, NEEDS_VERIFICATION)
             and isinstance(results[key].value, (int, float))]
    if not parts:
        _record(checks, results, f"total_{group}", False,
                f"{group.capitalize()} : total présent, ventilation absente ou non numérique.", [])
        return
    subtotal = sum(part.value for part in parts)
    complete = len(parts) == len(expected)
    certain = all(part.status == CONFIRMED for part in parts)
    # A partial subtotal equal to the total is compatible, but cannot validate
    # missing categories. Lower subtotals are unresolved, never a passed check.
    ok = complete and certain and subtotal == total.value
    message = (
        f"{group.capitalize()} : somme des catégories {format_number(subtotal, 'count')} "
        f"pour un total de {format_number(total.value, 'count')}."
    )
    if not complete:
        message += " Ventilation incomplète : total non vérifiable."
    elif subtotal != total.value:
        message += " Somme différente du total : ventilation incohérente."
    elif not certain:
        message += " Des colonnes restent ambiguës : contrôle non validé."
    keys = [part.indicator for part in parts]
    if subtotal > total.value:
        keys.append(f"{group}_total")
    _record(checks, results, f"total_{group}", ok, message, keys)


def check_ipa_assertion(results, checks):
    result = _confirmed(results, "presence_ipa")
    if not result:
        return
    text = normalize(f"{result.value} {result.evidence}")
    conditional = re.search(r"\bsi\s*(?:la\s*|le\s*|les\s*)?structure\b|\ben cas de\b", text)
    if conditional:
        _record(checks, results, "ipa_assertion", False,
                "Condition de barème IPA : la présence dans la structure n'est pas établie.",
                ["presence_ipa"])


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
    check_ipa_assertion(results, checks)
    return checks
