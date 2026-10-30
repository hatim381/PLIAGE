"""Exercise the complete retrieval + image review flow without a remote API."""

import base64
import json

import pymupdf
import pytest

from pliage.client import Conversation
from pliage.errors import PliageHTTPError, VisualReviewConfigError
from rag.pipeline import run_multi_rag
from rag.router import route
from rag.schemas import NEEDS_VERIFICATION
from ui.render import build_answer_markdown
from test_pipeline import FakeClient, TARGET
from test_visual_review import STAFF_TRANSCRIPT


@pytest.fixture
def options():
    with pymupdf.open() as doc:
        for page in range(7):
            doc.new_page().insert_text((30, 30), f'Page {page + 1}')
        return {'data': doc.tobytes(), 'name': TARGET, 'pages': '3,7', 'model': 'mistral-medium-2508'}


class VisionClient(FakeClient):
    def __init__(self, responses):
        super().__init__([])
        self.responses = iter(responses)
        self.image_calls = []

    def call_standard(self, messages, model_id, timeout=300):
        content = messages[0]['content']
        if not isinstance(content, list):
            return super().call_standard(messages, model_id, timeout)
        encoded = content[1]['image_url']['url'].split(',', 1)[1]
        assert base64.b64decode(encoded).startswith(b'\x89PNG\r\n\x1a\n')
        self.image_calls.append(model_id)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return json.dumps(response)


def staff_response():
    return {'transcription': STAFF_TRANSCRIPT, 'candidates': [
        {'indicator': 'associes_medecins_generalistes', 'value': 3,
         'quote': '3 médecins généralistes', 'value_type': 'establishment_value'},
        {'indicator': 'associes_paramedicaux', 'value': 5,
         'quote': '5 professionnels paramédicaux', 'value_type': 'establishment_value'},
    ]}


@pytest.mark.parametrize('updates,fragment', [
    ({'data': b''}, 'Joins le PDF'),
    ({'pages': ''}, 'Indique les pages'),
    ({'model': ' '}, 'modèle Pliage compatible images'),
    ({'pages': '8'}, 'hors du PDF'),
    ({'pages': '1-7'}, 'Maximum 5 pages'),
    ({'name': 'AUTRE.pdf'}, 'correspondre'),
    ({'data': b'invalid PDF'}, 'PDF illisible'),
])
def test_invalid_review_stops_before_any_remote_call(options, updates, fragment):
    options.update(updates)
    client = VisionClient([])
    with pytest.raises(VisualReviewConfigError, match=fragment):
        run_multi_rag(client, Conversation(), route(f'Dans {TARGET}, nombre de PS'), visual_review=options)
    assert client.turns == 0
    assert client.image_calls == []


def test_disabled_review_is_explicit_and_sends_no_images():
    client = VisionClient([])
    report = run_multi_rag(client, Conversation(), route(f'Dans {TARGET}, nombre de PS'))
    assert client.image_calls == []
    assert report.visual_review.status == 'disabled'
    assert 'Relecture visuelle : désactivée' in build_answer_markdown(report)


def test_successful_review_reaches_the_final_results_and_report(options):
    client = VisionClient([staff_response(), {'transcription': 'Aucun indicateur lisible', 'candidates': []}])
    report = run_multi_rag(client, Conversation(), route(f'Dans {TARGET}, nombre de PS'), visual_review=options)
    assert client.image_calls == ['mistral-medium-2508'] * 2
    assert report.visual_review.status == 'completed'
    assert report.visual_review.completed_pages == [3, 7]
    assert report.results['associes_medecins_generalistes'].value == 3
    assert report.results['associes_paramedicaux'].value == 5
    assert report.results['associes_paramedicaux'].status == NEEDS_VERIFICATION
    markdown = build_answer_markdown(report)
    assert 'Relecture visuelle : effectuée' in markdown
    assert 'Pages relues : 3, 7' in markdown
    assert 'image, page 3' in markdown


def test_later_page_failure_keeps_successful_page_and_sanitizes_errors(options):
    client = VisionClient([staff_response(), PliageHTTPError('SECRET_NOT_FOR_DISPLAY', status_code=403)])
    report = run_multi_rag(client, Conversation(), route(f'Dans {TARGET}, nombre de PS'), visual_review=options)
    assert report.visual_review.status == 'partial'
    assert report.visual_review.completed_pages == [3]
    assert list(report.visual_review.failures) == [7]
    assert report.results['associes_medecins_generalistes'].value == 3
    assert any(e.chunk_id == 'V3' for e in report.evidence)
    markdown = build_answer_markdown(report)
    assert 'Relecture visuelle : partielle' in markdown
    assert 'Page 7 : Accès au modèle refusé' in markdown
    assert 'SECRET_NOT_FOR_DISPLAY' not in markdown + json.dumps(report.debug)


def test_all_pages_fail_without_losing_text_results(options):
    client = VisionClient([ValueError('invalid response'), TimeoutError('timeout')])
    client.extraction = [{'indicator': 'patientele_reference', 'value': 2133, 'chunk': 'C1',
                         'quote': 'Patientèle de référence : 2 133', 'value_type': 'establishment_value'}]
    report = run_multi_rag(client, Conversation(), route(f'Dans {TARGET}, patientèle'), visual_review=options)
    assert report.visual_review.status == 'failed'
    assert report.visual_review.completed_pages == []
    assert report.results['patientele_reference'].value == 2133
    assert not any(e.chunk_id.startswith('V') for e in report.evidence)
    markdown = build_answer_markdown(report)
    assert 'Relecture visuelle : échouée' in markdown
    assert 'Aucune valeur visuelle exploitable' in markdown


def test_read_page_without_usable_candidate_is_not_reported_as_improvement(options):
    options['pages'] = '3'
    response = staff_response()
    response['candidates'][0]['value'] = 900
    response['candidates'] = response['candidates'][:1]
    client = VisionClient([response])
    report = run_multi_rag(client, Conversation(), route(f'Dans {TARGET}, nombre de PS'), visual_review=options)
    assert report.visual_review.status == 'completed'
    assert report.visual_review.completed_pages == [3]
    assert report.visual_review.candidate_count == 1
    assert report.visual_review.usable_indicators == []
    assert 'Aucune valeur visuelle exploitable' in build_answer_markdown(report)
