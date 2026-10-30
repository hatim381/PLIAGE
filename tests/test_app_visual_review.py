"""Protect against a checked image option silently becoming a text-only call."""

from pathlib import Path

import pytest

from pliage import config
from pliage.client import PliageClient

AppTest = pytest.importorskip('streamlit.testing.v1').AppTest
APP = Path(__file__).resolve().parents[1] / 'app.py'


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(config, 'API_TOKEN', 'local-test-only')
    monkeypatch.delenv('VISION_MODEL', raising=False)
    calls = []

    def remote_call(*args, **kwargs):
        calls.append(True)
        raise AssertionError('No remote call expected')

    monkeypatch.setattr(PliageClient, 'run_openwebui_turn', remote_call)
    monkeypatch.setattr(PliageClient, 'call_standard', remote_call)
    return AppTest.from_file(str(APP)).run(), calls


def test_checked_review_without_upload_is_blocked_in_ui(app):
    at, calls = app
    assert not at.exception
    assert at.text_input(key='visual_model').value == 'mistral-medium-2508'
    at.text_input(key='visual_pages').set_value('3,7')
    at.checkbox(key='visual_enabled').check().run()
    at.chat_input[0].set_value('Dans MSP.pdf, nombre de PS').run()
    assert not at.exception
    assert any('Joins le PDF' in error.value for error in at.error)
    assert calls == []
    assert not any(m.get('report') for m in at.session_state.messages)


@pytest.mark.parametrize('mode', ['other_model', 'multi_rag_off'])
def test_checked_review_cannot_be_ignored_by_a_different_chat_mode(app, mode):
    at, calls = app
    if mode == 'other_model':
        at.selectbox[0].select('💬 Le Généraliste').run()
    else:
        at.toggle[1].set_value(False).run()
    at.checkbox(key='visual_enabled').check().run()
    at.chat_input[0].set_value('Dans MSP.pdf, nombre de PS').run()
    assert not at.exception
    assert any('Sélectionne Le Premium' in error.value for error in at.error)
    assert calls == []


def test_uploaded_images_reach_premium_flow_and_survive_new_conversation(app, monkeypatch):
    import pymupdf
    from test_pipeline import TARGET
    from test_visual_review_flow import VisionClient, staff_response

    at, calls = app
    if not hasattr(at, 'file_uploader'):
        pytest.skip('File uploads require a newer Streamlit AppTest.')
    responses = [staff_response(), {'transcription': 'Aucun indicateur lisible', 'candidates': []}] * 2
    client = VisionClient(responses)
    monkeypatch.setattr('pliage.client.PliageClient', lambda token: client)
    with pymupdf.open() as doc:
        for _ in range(7):
            doc.new_page()
        pdf = doc.tobytes()
    at.file_uploader(key='visual_pdf').set_value((TARGET, pdf, 'application/pdf'))
    at.text_input(key='visual_pages').set_value('3,7')
    at.checkbox(key='visual_enabled').check().run()
    assert at.selectbox[0].value == '⭐ Le Premium'
    for turn in range(2):
        at.chat_input[0].set_value(f'Dans {TARGET}, nombre de PS').run()
        assert not at.exception
        assert not at.error
        report = at.session_state.messages[-1]['report']
        assert report.visual_review.completed_pages == [3, 7]
        assert report.results['associes_medecins_generalistes'].value == 3
        assert 'image, page 3' in report.markdown
        if turn == 0:
            at.button[0].click().run()
            assert at.session_state.messages == []
            assert at.checkbox(key='visual_enabled').value
            assert at.file_uploader(key='visual_pdf').value is not None
    assert client.image_calls == ['mistral-medium-2508'] * 4
    assert calls == []
