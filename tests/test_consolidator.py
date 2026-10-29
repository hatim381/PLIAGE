from rag.catalog import INDICATORS_BY_KEY, indicators_for
from rag.consolidator import consolidate
from rag.extractor import classify_value_context, parse_extraction_response, quote_in_chunk, to_candidates
from rag.schemas import (
    CONFIRMED, CONFLICT, ESTABLISHMENT, METHODOLOGICAL, NEEDS_VERIFICATION, NOT_FOUND,
    POINTS, Evidence,
)
from rag.text import find_numbers, parse_number

PATIENTELE = INDICATORS_BY_KEY["patientele_reference"]

TABLE = "| Patientèle de référence | 2 133 |\n| File active | 2 191 |"
METHODO = "Le montant est calculé pour une patientèle de référence de 4 000 patients."


def evidence(*contents):
    return [Evidence(chunk_id=f"C{i}", content=c, source="MSP.pdf", file_id="f1",
                     queries=["patientèle"]) for i, c in enumerate(contents, start=1)]


def raw(indicator, value, chunk, quote, value_type=ESTABLISHMENT, period=None):
    return {"indicator": indicator, "value": value, "chunk": chunk, "quote": quote,
            "value_type": value_type, "period": period}


def results_for(raws, ev, families=("patientele",)):
    return consolidate(to_candidates(raws, ev), indicators_for(families))


def test_french_numbers():
    assert parse_number("2 133") == 2133
    assert parse_number("52 110,10 €") == 52110.10
    assert parse_number("6 %") == 6
    assert parse_number("21513.77") == 21513.77
    assert parse_number("1.500") == 1500
    assert 2133 in find_numbers("Patientèle : 2 133 patients")


def test_methodological_phrase_is_not_establishment_value():
    assert classify_value_context(METHODO, 4000, PATIENTELE) == METHODOLOGICAL
    assert classify_value_context("Patientèle de référence : 2 133", 2133, PATIENTELE) is None


def test_llm_mislabel_is_reclassified_by_context():
    ev = evidence(METHODO)
    results = results_for([raw("patientele_reference", 4000, "C1", METHODO)], ev)
    result = results["patientele_reference"]
    assert result.status == NOT_FOUND
    assert result.value is None
    assert result.candidates[0].value_type == METHODOLOGICAL


def test_conflict_keeps_both_and_selects_establishment_value():
    ev = evidence(TABLE, METHODO)
    results = results_for([
        raw("patientele_reference", 2133, "C1", "Patientèle de référence | 2 133"),
        raw("patientele_reference", 4000, "C2", METHODO, value_type=METHODOLOGICAL),
    ], ev)
    result = results["patientele_reference"]
    assert result.status == CONFIRMED
    assert result.value == 2133
    assert result.value_type == ESTABLISHMENT
    assert {(c.value, c.value_type) for c in result.candidates} == {
        (2133, ESTABLISHMENT), (4000, METHODOLOGICAL),
    }
    assert any("4 000" in m for m in result.messages)


def test_two_establishment_values_are_a_conflict():
    ev = evidence("Patientèle de référence : 2 133", "Patientèle de référence : 2 500")
    results = results_for([
        raw("patientele_reference", 2133, "C1", "Patientèle de référence : 2 133"),
        raw("patientele_reference", 2500, "C2", "Patientèle de référence : 2 500"),
    ], ev)
    assert results["patientele_reference"].status == CONFLICT
    assert results["patientele_reference"].value is None


def test_absence_is_not_found_never_zero():
    results = results_for([], evidence(TABLE))
    for key in ("ame_patients", "ame_taux", "c2s_patients"):
        assert results[key].status == NOT_FOUND
        assert results[key].value is None


def test_explicit_zero_is_kept():
    ev = evidence("Patients AME : 0 (0 %)")
    results = results_for([raw("ame_patients", 0, "C1", "Patients AME : 0")], ev)
    assert results["ame_patients"].status == CONFIRMED
    assert results["ame_patients"].value == 0


def test_hallucinated_value_is_not_confirmed():
    ev = evidence(TABLE)
    results = results_for([raw("c2s_patients", 140, "C1", "Patients C2S : 140")], ev)
    assert results["c2s_patients"].status == NEEDS_VERIFICATION
    assert results["c2s_patients"].value is None


def test_points_are_not_euros():
    ind = INDICATORS_BY_KEY["si_remuneration"]
    assert classify_value_context("Système d'information : 500 points", 500, ind) == POINTS


def test_text_value_with_hedging_is_rejected():
    ev = evidence("Si la structure intègre un IPA, 1 700 points supplémentaires.")
    results = results_for(
        [raw("presence_ipa", "Probablement aucun IPA", "C1", "Si la structure intègre un IPA")],
        ev, families=("ipa_formation",),
    )
    assert results["presence_ipa"].status != CONFIRMED


def test_conditional_text_is_methodological():
    ev = evidence("Si la structure intègre un IPA, 1 700 points supplémentaires.")
    results = results_for(
        [raw("presence_ipa", "Oui", "C1", "Si la structure intègre un IPA")],
        ev, families=("ipa_formation",),
    )
    assert results["presence_ipa"].status == NOT_FOUND


OCR_STAFF = (
    "5 professionnels associés signataires\n4 professionnels vacataires\n"
    "médecins généralistes\nprofessionnels paramédicaux\n0 médecin généraliste\n"
    "3 professionnels paramédicaux\n1 autre professionnel"
)


def test_ambiguous_associes_vacataires_column_is_not_confirmed():
    ev = evidence(OCR_STAFF)
    results = results_for([
        raw("associes_total", 5, "C1", "5 professionnels associés signataires"),
        raw("associes_medecins_generalistes", 0, "C1", "0 médecin généraliste"),
    ], ev, families=("professionnels",))
    assert results["associes_total"].status == CONFIRMED
    mg = results["associes_medecins_generalistes"]
    assert mg.status == NEEDS_VERIFICATION
    assert "ambiguë" in mg.messages[0]


def test_bare_label_is_not_an_establishment_value():
    chunk = ("La structure dispose d'un système d'information labellisé niveau « standard » par l'ANS. "
             "Grille : niveau standard / niveau avancé")
    results = results_for([
        raw("si_labellisation", "standard", "C1",
            "La structure dispose d'un système d'information labellisé niveau « standard » par l'ANS"),
        raw("si_labellisation", "avancé", "C1", "niveau avancé"),
    ], evidence(chunk), families=("systeme_information",))
    si = results["si_labellisation"]
    assert si.status == CONFIRMED
    assert si.value == "standard"
    assert len(si.candidates) == 2


def test_quote_matching_tolerates_table_formatting():
    assert quote_in_chunk("Patientèle de référence 2 133", TABLE)
    assert not quote_in_chunk("Patientèle de référence 9 999 patients déclarés", TABLE)


def test_parse_extraction_response_with_fences():
    text = '```json\n{"candidates": [{"indicator": "file_active", "value": 2191}]}\n```'
    assert parse_extraction_response(text)[0]["value"] == 2191


def test_ocr_prefix_variant_of_an_amount_is_not_a_conflict():
    ev = evidence("Lettre : s'élève 412 345,67 € au titre de l'exercice.",
                  "Total de la rémunération Exercice 2023 12 345,67 €")
    raws = [
        raw("aci_remuneration_totale", 412345.67, "C1", "s'élève 412 345,67 €"),
        raw("aci_remuneration_totale", 12345.67, "C2", "Total de la rémunération Exercice 2023 12 345,67 €"),
    ]
    result = results_for(raws, ev, families=("remuneration",))["aci_remuneration_totale"]
    assert result.status == CONFIRMED
    assert result.value == 12345.67


def test_unrelated_amounts_remain_a_conflict():
    ev = evidence("Total 12 345,67 €", "Total 98 765,43 €")
    raws = [
        raw("aci_remuneration_totale", 12345.67, "C1", "Total 12 345,67 €"),
        raw("aci_remuneration_totale", 98765.43, "C2", "Total 98 765,43 €"),
    ]
    result = results_for(raws, ev, families=("remuneration",))["aci_remuneration_totale"]
    assert result.status == CONFLICT
