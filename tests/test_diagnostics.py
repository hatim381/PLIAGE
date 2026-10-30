from rag.catalog import INDICATORS_BY_KEY
from rag.schemas import Candidate, Evidence, ExtractionReport, IndicatorResult, RoutePlan
from ui.render import diagnostic_entries, build_answer_markdown


def test_diagnostic_preserves_full_passage_and_rejected_candidate():
    text = "Associés et vacataires\n" + "texte " * 1000 + "FIN"
    candidate = Candidate(indicator="associes_paramedicaux", value=1, chunk_id="C1",
                          quote="1 paramédical", rejection="colonne ambiguë")
    result = IndicatorResult(indicator=candidate.indicator, label="Paramédicaux",
                             candidates=[candidate])
    report = ExtractionReport(plan=RoutePlan(question="effectifs", families=["professionnels"]),
                              results={result.indicator: result},
                              evidence=[Evidence(chunk_id="C1", content=text, source="MSP.pdf", page="3")])
    entry = diagnostic_entries(report)[0]
    assert entry["passages_recus"][0]["texte_integral"] == text
    assert entry["passages_recus"][0]["page"] == "3"
    assert entry["candidats"][0]["rejet"] == "colonne ambiguë"


def test_missing_candidate_exposes_available_family_passages():
    key = "presence_ipa"
    report = ExtractionReport(plan=RoutePlan(question="IPA", families=["ipa_formation"]),
                              results={key: IndicatorResult(indicator=key, label=INDICATORS_BY_KEY[key].label)},
                              evidence=[Evidence(chunk_id="C1", content="IPA grille", source="MSP.pdf",
                                                 families=["ipa_formation"])])
    assert diagnostic_entries(report)[0]["passages_recus"][0]["page"] is None
