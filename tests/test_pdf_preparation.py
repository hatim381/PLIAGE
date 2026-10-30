import pymupdf
import pytest

from pliage.pdf_preparation import open_pdf, preview_page, rotate_pdf


def sample_pdf():
    with pymupdf.open() as document:
        for rotation in (0, 90, 180):
            page = document.new_page(width=200, height=300)
            page.insert_text((30, 50), "Associated 3 / Visiting 0")
            page.set_rotation(rotation)
        return document.tobytes()


def test_rotation_preserves_content_other_pages_and_input():
    original = sample_pdf()
    result = rotate_pdf(original, {1: 270})
    with open_pdf(result) as document:
        assert [page.rotation for page in document] == [0, 0, 180]
        assert all("Associated 3 / Visiting 0" in page.get_text() for page in document)
    with open_pdf(original) as document:
        assert document[1].rotation == 90


def test_preview_matches_export_orientation():
    original = sample_pdf()
    corrected = rotate_pdf(original, {0: 90})
    assert preview_page(original, 0, 90) == preview_page(corrected, 0)


@pytest.mark.parametrize("corrections", [{3: 90}, {-1: 90}, {0: 45}])
def test_invalid_corrections(corrections):
    with pytest.raises(ValueError):
        rotate_pdf(sample_pdf(), corrections)


def test_osd_suggestion_and_low_confidence(monkeypatch):
    from types import SimpleNamespace
    from pliage import pdf_preparation as module
    monkeypatch.setattr(module.shutil, "which", lambda _: "tesseract")
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout="Rotate: 270\nOrientation confidence: 10.2\n"))
    assert module.detect_rotation(sample_pdf(), 0) == 270
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout="Rotate: 90\nOrientation confidence: 1.2\n"))
    assert module.detect_rotation(sample_pdf(), 0) is None
