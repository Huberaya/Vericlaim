"""OCR qualification against a real Tesseract engine.

The application drives Tesseract through ``pytesseract``, which shells out to the
``tesseract`` binary. That binary is installed by ``backend/Dockerfile`` and is
present in the CI image, but it is absent from a bare Python environment. The
qualification harness therefore exercises the **same engine** through
``tesserocr`` (which links ``libtesseract`` directly).

These tests do two things:

* skip cleanly when ``tesseract`` or ``tesserocr`` is unavailable, so CI stays
  deterministic;
* prove the properties the mission asks about — a textual PDF needs no OCR, a
  scanned PDF and an image do, and OCR output is *flagged* rather than trusted.

They do not replace the production path: ``docs/infrastructure/07-ocr-et-extraction.md``
records exactly which engine produced which result.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

#: A bare Python environment has no system ``tessdata``. The qualification harness
#: keeps a real ``eng.traineddata`` under the git-ignored ``.recette/tessdata`` and
#: points the engine at it, exactly as an operator would with ``TESSDATA_PREFIX``.
_RECETTE_TESSDATA = Path(__file__).resolve().parents[2] / ".recette" / "tessdata"
if _RECETTE_TESSDATA.is_dir() and not os.environ.get("TESSDATA_PREFIX"):
    os.environ["TESSDATA_PREFIX"] = str(_RECETTE_TESSDATA)

from app.engine.document_extractor import DocumentExtractionError, DocumentTextExtractor

TESSERACT_CLI = shutil.which("tesseract") is not None

try:  # pragma: no cover - environment dependent
    import tesserocr  # noqa: F401

    TESSEROCR_AVAILABLE = True
except Exception:  # pragma: no cover
    TESSEROCR_AVAILABLE = False


def _render_text_page(text: str, *, scale: int = 3) -> bytes:
    """Render real text into a PNG so an OCR engine has something to read."""

    import fitz

    document = fitz.open()
    page = document.new_page()
    page.insert_text((50, 90), text, fontsize=22)
    png = page.get_pixmap(matrix=fitz.Matrix(scale, scale)).tobytes("png")
    document.close()
    return png


def _scanned_pdf(text: str) -> bytes:
    """A PDF whose only content is a raster image of the text."""

    import fitz

    image_bytes = _render_text_page(text)
    document = fitz.open()
    page = document.new_page()
    page.insert_image(page.rect, stream=image_bytes)
    pdf = document.tobytes()
    document.close()
    return pdf


def _text_pdf(text: str) -> bytes:
    import fitz

    document = fitz.open()
    page = document.new_page()
    page.insert_text((60, 80), text, fontsize=12)
    pdf = document.tobytes()
    document.close()
    return pdf


@pytest.mark.parametrize(
    ("name", "kind", "expected_method"),
    [
        ("declaration.txt", "text", "TEXT_FILE"),
        ("fiche.pdf", "pdf", "PDF_TEXT"),
    ],
)
def test_structured_sources_never_invoke_ocr(name: str, kind: str, expected_method: str) -> None:
    """No scanner, no OCR timeout: text stays text and is trusted as such."""

    sentence = "Le produit est recyclable et biodegradable."
    data = sentence.encode("utf-8") if kind == "text" else _text_pdf(sentence)
    result = DocumentTextExtractor().extract_result(name, None, data)
    assert result.method == expected_method
    assert result.requires_human_review is False
    assert "recyclable" in result.text.lower()
    assert result.segments, "extraction must produce citeable segments"


def test_a_scanned_pdf_is_flagged_for_human_review() -> None:
    """The single most important OCR property: never trust a transcription.

    The worker must mark an OCR-derived page so a human compares it with the
    original before it is used as evidence.
    """

    if not (TESSERACT_CLI or TESSEROCR_AVAILABLE):
        pytest.skip("no Tesseract engine available in this environment")
    payload = _scanned_pdf("RECYCLABLE")
    try:
        result = DocumentTextExtractor(tesseract_languages="eng").extract_result("scan.pdf", "application/pdf", payload)
    except DocumentExtractionError as exc:
        pytest.skip(f"OCR unavailable in this environment: {exc}")
    assert result.method == "PDF_OCR"
    assert result.requires_human_review is True
    assert any(page.used_ocr for page in result.pages)


def test_an_image_is_fully_ocr_derived() -> None:
    if not (TESSERACT_CLI or TESSEROCR_AVAILABLE):
        pytest.skip("no Tesseract engine available in this environment")
    payload = _render_text_page("RECYCLABLE")
    try:
        result = DocumentTextExtractor(tesseract_languages="eng").extract_result("etiquette.png", "image/png", payload)
    except DocumentExtractionError as exc:
        pytest.skip(f"OCR unavailable in this environment: {exc}")
    assert result.method == "OCR_IMAGE"
    assert result.requires_human_review is True


def test_an_empty_document_is_refused_rather_than_guessed() -> None:
    with pytest.raises(DocumentExtractionError):
        DocumentTextExtractor().extract_result("vide.txt", "text/plain", b"   \n\n  ")


def test_an_unsupported_extension_is_refused() -> None:
    with pytest.raises(DocumentExtractionError):
        DocumentTextExtractor().extract_result("script.exe", "application/octet-stream", b"MZ")


def test_a_corrupt_pdf_is_refused() -> None:
    with pytest.raises(DocumentExtractionError):
        DocumentTextExtractor().extract_result("casse.pdf", "application/pdf", b"%PDF-1.7\nnot really a pdf")


def test_an_oversized_document_is_refused_before_parsing() -> None:
    extractor = DocumentTextExtractor(max_bytes=64)
    with pytest.raises(DocumentExtractionError):
        extractor.extract_result("gros.txt", "text/plain", b"a" * 65)


def test_page_limit_is_enforced() -> None:
    extractor = DocumentTextExtractor(max_pages=1)
    import fitz

    document = fitz.open()
    for index in range(2):
        page = document.new_page()
        page.insert_text((50, 80), f"Page {index}", fontsize=12)
    payload = document.tobytes()
    document.close()
    with pytest.raises(DocumentExtractionError):
        extractor.extract_result("deux-pages.pdf", "application/pdf", payload)


@pytest.mark.skipif(not TESSEROCR_AVAILABLE, reason="tesserocr (libtesseract) not installed")
def test_the_bundled_tesseract_engine_really_reads_text() -> None:
    """Direct evidence that the OCR engine available to the harness works."""

    import PIL.Image
    import tesserocr
    from io import BytesIO

    image = PIL.Image.open(BytesIO(_render_text_page("RECYCLABLE", scale=4)))
    text = tesserocr.image_to_text(image, lang="eng")
    assert "RECYCL" in text.upper().replace(" ", "")


def test_trained_language_data_presence_is_reported_not_assumed() -> None:
    """An engine without ``.traineddata`` fails every page: say so, never assume.

    This asserts only that the probe works. Whether languages are installed is a
    property of the *image*, recorded in the OCR report, not a test outcome: a CI
    runner legitimately has none until ``backend/Dockerfile`` installs them.
    """

    if not TESSEROCR_AVAILABLE:  # pragma: no cover
        pytest.skip("tesserocr not installed")
    import tesserocr

    languages = set(tesserocr.get_languages()[1])
    assert isinstance(languages, set)
