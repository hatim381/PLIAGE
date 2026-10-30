import json

import pymupdf
import pytest

from rag.catalog import indicators_for
from rag.schemas import CONFIRMED, CONFLICT, NEEDS_VERIFICATION, Evidence, ExtractionReport, RoutePlan
from rag.consolidator import consolidate
from rag.extractor import to_candidates
from rag.visual_review import parse_pages, review_pages, apply_visual_review
from rag.validator import validate


def test_protocol_ocr_seven_vs_300_points_is_flagged():
    evidence = [Evidence(chunk_id='C4', source='MSP.pdf', content=(
        'Protocoles pluri-professionnels : 100 points/protocole\n'
        'Nombre de protocoles réalisés et rémunérés 7 300 2 100,00€'))]
    raw = [{'indicator': key, 'value': 7, 'value_type': 'establishment_value', 'chunk': 'C4',
            'quote': 'Nombre de protocoles réalisés et rémunérés 7'}
           for key in ('protocoles_realises', 'protocoles_remuneres')]
    results = consolidate(to_candidates(raw, evidence), indicators_for(['coordination']))
    checks = validate(results, evidence)
    assert results['protocoles_realises'].status == NEEDS_VERIFICATION
    assert results['protocoles_remuneres'].value == 7  # never silently turn into 3
    assert any(c.name == 'points_protocoles_realises' and not c.ok for c in checks)


@pytest.mark.parametrize('pages', ['0', '6', '2-1', '1-6', 'abc'])
def test_invalid_pages(pages):
    with pytest.raises(ValueError):
        parse_pages(pages, 5)


def test_visual_review_uses_image_and_does_not_auto_confirm_new_value():
    plan = RoutePlan(question='protocoles', families=['coordination'], target_file='MSP.pdf')
    indicators = indicators_for(plan.families)
    with pymupdf.open() as document:
        document.new_page().insert_text((20, 20), 'Protocoles 3')
        pdf = document.tobytes()
    class Client:
        def call_standard(self, messages, model, timeout):
            assert messages[0]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
            return json.dumps({'transcription': 'Nombre de protocoles réalisés et rémunérés : 3',
                               'candidates': [{'indicator': 'protocoles_realises', 'value': 3,
                                               'quote': 'Nombre de protocoles réalisés et rémunérés : 3',
                                               'value_type': 'establishment_value'}]})
    evidence, candidates = review_pages(Client(), plan, indicators,
        {'name': 'MSP.pdf', 'data': pdf, 'pages': '1', 'model': 'configured-vision'}, lambda _: None)
    report = ExtractionReport(plan=plan, results=consolidate([], indicators))
    apply_visual_review(report, evidence, candidates, indicators)
    assert report.results['protocoles_realises'].value == 3
    assert report.results['protocoles_realises'].status == NEEDS_VERIFICATION
    assert report.evidence[0].page == '1'


def test_different_visual_and_ocr_values_remain_conflict():
    plan = RoutePlan(question='protocoles', families=['coordination'])
    indicators = indicators_for(plan.families)
    def make(value, chunk):
        item = Evidence(chunk_id=chunk, source='MSP.pdf', content=f'Protocoles réalisés : {value}')
        candidates = to_candidates([{'indicator': 'protocoles_realises', 'value': value,
            'quote': item.content, 'chunk': chunk, 'value_type': 'establishment_value'}], [item])
        return item, candidates
    ocr, old = make(7, 'C1')
    visual, new = make(3, 'V1')
    report = ExtractionReport(plan=plan, evidence=[ocr], candidates=old, results=consolidate(old, indicators))
    apply_visual_review(report, [visual], new, indicators)
    assert report.results['protocoles_realises'].status == CONFLICT


def test_other_pdf_is_rejected_before_api_call():
    with pytest.raises(ValueError, match='correspondre'):
        review_pages(None, RoutePlan(question='', families=[], target_file='VERRIERES.pdf'), [],
                     {'name': 'AUTRE.pdf', 'model': 'vision'}, lambda _: None)


def test_consistent_protocol_points_remain_confirmed():
    item = Evidence(chunk_id='C1', source='MSP.pdf', content=(
        'Protocoles : 100 points/protocole\n'
        'Nombre de protocoles réalisés et rémunérés 3 300 2 100,00€'))
    candidates = to_candidates([{'indicator': 'protocoles_realises', 'value': 3,
        'quote': 'Nombre de protocoles réalisés et rémunérés 3', 'chunk': 'C1',
        'value_type': 'establishment_value'}], [item])
    results = consolidate(candidates, indicators_for(['coordination']))
    assert any(c.ok and c.name == 'points_protocoles_realises' for c in validate(results, [item]))
    assert results['protocoles_realises'].status == CONFIRMED
