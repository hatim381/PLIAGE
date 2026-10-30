"""Local PDF orientation helpers. No upload or OCR service is called here."""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf


MAX_PAGES = 100


def open_pdf(data):
    document = pymupdf.open(stream=data, filetype="pdf")
    if document.needs_pass or not 1 <= len(document) <= MAX_PAGES:
        document.close()
        raise ValueError("Le PDF doit être non protégé et contenir entre 1 et 100 pages.")
    return document


def preview_page(data, page_index, correction=0):
    with open_pdf(data) as document:
        page = document[page_index]
        page.set_rotation((page.rotation + correction) % 360)
        return page.get_pixmap(matrix=pymupdf.Matrix(1.2, 1.2)).tobytes("png")


def rotate_pdf(data, corrections):
    """Apply clockwise corrections to zero-based pages, preserving existing rotation."""
    with open_pdf(data) as document:
        for index, correction in corrections.items():
            if not isinstance(index, int) or not 0 <= index < len(document):
                raise ValueError("Numéro de page invalide.")
            if correction not in (0, 90, 180, 270):
                raise ValueError("Rotation invalide.")
            page = document[index]
            page.set_rotation((page.rotation + correction) % 360)
        return document.tobytes(garbage=4, deflate=True)


def detect_rotation(data, page_index):
    """Optional Tesseract OSD suggestion; low-confidence/blank pages stay unclassified."""
    executable = shutil.which("tesseract")
    if not executable:
        raise RuntimeError("Tesseract absent : utilise les corrections manuelles.")
    with tempfile.TemporaryDirectory(prefix="pliage-osd-") as directory:
        path = Path(directory) / "page.png"
        with open_pdf(data) as document:
            document[page_index].get_pixmap(dpi=150).save(path)
        result = subprocess.run(
            [executable, str(path), "stdout", "--psm", "0"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    rotation = re.search(r"^Rotate:\s*(0|90|180|270)\s*$", result.stdout, re.MULTILINE)
    confidence = re.search(r"^Orientation confidence:\s*([\d.]+)", result.stdout, re.MULTILINE)
    if result.returncode or not rotation or not confidence or float(confidence[1]) < 5:
        return None
    return int(rotation[1])
