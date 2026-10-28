from rag.catalog import INDICATORS_BY_KEY
from rag.schemas import CONFIRMED, NEEDS_VERIFICATION, NOT_FOUND, IndicatorResult
from rag.validator import validate


def confirmed(**values):
    return {
        key: IndicatorResult(indicator=key, label=INDICATORS_BY_KEY[key].label, status=CONFIRMED, value=value)
        for key, value in values.items()
    }


def check(checks, name):
    return next(c for c in checks if c.name == name)


def test_aci_advance_plus_balance_equals_total():
    results = confirmed(aci_remuneration_totale=52110.10, aci_avance_exercice=21513.77, aci_solde_exercice=30596.33)
    checks = validate(results)
    assert check(checks, "aci_avance_plus_solde").ok
    assert all(r.status == CONFIRMED for r in results.values())


def test_aci_rounding_is_tolerated():
    results = confirmed(aci_remuneration_totale=52110, aci_avance_exercice=21513.77, aci_solde_exercice=30596.33)
    assert check(validate(results), "aci_avance_plus_solde").ok


def test_aci_mismatch_needs_verification():
    results = confirmed(aci_remuneration_totale=60000, aci_avance_exercice=21513.77, aci_solde_exercice=30596.33)
    assert not check(validate(results), "aci_avance_plus_solde").ok
    assert results["aci_remuneration_totale"].status == NEEDS_VERIFICATION


def test_advance_presented_as_total_is_flagged():
    results = confirmed(aci_remuneration_totale=21513.77, aci_avance_exercice=21513.77)
    validate(results)
    assert results["aci_remuneration_totale"].status == NEEDS_VERIFICATION


def test_impossible_percentage_is_flagged():
    results = confirmed(c2s_taux=140)
    checks = validate(results)
    assert not check(checks, "pourcentage").ok
    assert results["c2s_taux"].status == NEEDS_VERIFICATION
    assert results["c2s_taux"].value == 140  # signalée, pas effacée


def test_c2s_coherence_tolerates_rounding():
    results = confirmed(c2s_patients=140, c2s_taux=6, patientele_reference=2133, file_active=2191)
    assert check(validate(results), "coherence_c2s").ok


def test_c2s_incoherence_is_flagged():
    results = confirmed(c2s_patients=140, c2s_taux=25, patientele_reference=2133)
    assert not check(validate(results), "coherence_c2s").ok
    assert results["c2s_taux"].status == NEEDS_VERIFICATION


def test_ame_zero_is_coherent():
    results = confirmed(ame_patients=0, ame_taux=0, patientele_reference=2133)
    assert check(validate(results), "coherence_ame").ok


def test_associates_and_vacataires_are_checked_separately():
    results = confirmed(
        associes_total=5, associes_medecins_generalistes=2, associes_paramedicaux=3,
        vacataires_total=4, vacataires_paramedicaux=3, vacataires_autres=1,
    )
    checks = validate(results)
    assert check(checks, "total_associes").ok
    assert check(checks, "total_vacataires").ok


def test_category_sum_above_total_is_flagged():
    results = confirmed(associes_total=2, associes_medecins_generalistes=2, associes_paramedicaux=3)
    assert not check(validate(results), "total_associes").ok


def test_non_integer_count_is_flagged():
    results = confirmed(file_active=2191.5)
    validate(results)
    assert results["file_active"].status == NEEDS_VERIFICATION


def test_not_found_is_left_untouched():
    results = {"ame_taux": IndicatorResult(indicator="ame_taux", label="AME", status=NOT_FOUND)}
    assert validate(results) == []
    assert results["ame_taux"].value is None
