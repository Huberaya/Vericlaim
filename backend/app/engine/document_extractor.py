"""Bounded text extraction for already-clean document bytes.

The extractor is deliberately storage-agnostic. Callers must establish the
security boundary first: only bytes promoted after the MinIO quarantine and
ClamAV checks may enter the durable worker. OCR remains assistive evidence; any
page that required OCR is flagged for human review rather than treated as an
authoritative transcription.
"""

from __future__ import annotations

import math
import re
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import ClassVar

from app.core.config import settings


class DocumentExtractionError(ValueError):
    """The clean source cannot be converted into a bounded usable transcript."""


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    text: str
    used_ocr: bool


@dataclass(frozen=True)
class ExtractedSegment:
    page_number: int
    segment_type: str
    text: str
    start_offset: int
    end_offset: int


@dataclass(frozen=True)
class ExtractionResult:
    """Canonical extraction result persisted by the asynchronous worker."""

    text: str
    method: str
    page_count: int
    pages: tuple[ExtractedPage, ...]
    segments: tuple[ExtractedSegment, ...]
    requires_human_review: bool


class DocumentTextExtractor:
    ALLOWED_EXTENSIONS: ClassVar[frozenset[str]] = frozenset(
        {".txt", ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"}
    )

    def __init__(
        self,
        *,
        max_bytes: int | None = None,
        max_pages: int | None = None,
        max_chars: int | None = None,
        max_image_pixels: int | None = None,
        ocr_timeout_seconds: int | None = None,
        ocr_render_scale: int | None = None,
        segment_max_chars: int | None = None,
        tesseract_languages: str | None = None,
    ) -> None:
        self.max_bytes = settings.max_upload_bytes if max_bytes is None else max_bytes
        self.max_pages = settings.max_pdf_pages if max_pages is None else max_pages
        self.max_chars = settings.max_source_chars if max_chars is None else max_chars
        self.max_image_pixels = settings.document_max_image_pixels if max_image_pixels is None else max_image_pixels
        self.ocr_timeout_seconds = (
            settings.document_ocr_timeout_seconds if ocr_timeout_seconds is None else ocr_timeout_seconds
        )
        self.ocr_render_scale = settings.document_ocr_render_scale if ocr_render_scale is None else ocr_render_scale
        self.segment_max_chars = settings.document_segment_max_chars if segment_max_chars is None else segment_max_chars
        self.tesseract_languages = settings.tesseract_languages if tesseract_languages is None else tesseract_languages

    def extract(self, filename: str, content_type: str | None, payload: bytes) -> tuple[str, str]:
        """Compatibility façade for the legacy ephemeral evaluation endpoint."""
        result = self.extract_result(filename, content_type, payload)
        return result.text, result.method

    def extract_result(self, filename: str, content_type: str | None, payload: bytes) -> ExtractionResult:
        del content_type  # MIME/signature coherence is established by the secure ingestion boundary.
        if len(payload) > self.max_bytes:
            raise DocumentExtractionError(f"Fichier trop volumineux: maximum {self.max_bytes} octets.")
        extension = Path(filename or "").suffix.lower()
        if extension not in self.ALLOWED_EXTENSIONS:
            raise DocumentExtractionError("Type de fichier non pris en charge. Utilisez TXT, PDF, PNG, JPG, TIFF ou WEBP.")
        if not payload:
            raise DocumentExtractionError("Le document est vide.")

        if extension == ".txt":
            pages = [ExtractedPage(page_number=1, text=self._decode_text(payload), used_ocr=False)]
            method = "TEXT_FILE"
        elif extension == ".pdf":
            pages = self._extract_pdf_pages(payload)
            method = "PDF_OCR" if any(page.used_ocr for page in pages) else "PDF_TEXT"
        else:
            pages = [ExtractedPage(page_number=1, text=self._extract_image_text(payload), used_ocr=True)]
            method = "OCR_IMAGE"

        canonical_text, segments = self._canonicalize_pages(pages)
        if not canonical_text:
            raise DocumentExtractionError("Aucun texte exploitable n'a été extrait du document.")
        if len(canonical_text) > self.max_chars:
            raise DocumentExtractionError(f"Texte extrait trop long: maximum {self.max_chars} caractères.")
        return ExtractionResult(
            text=canonical_text,
            method=method,
            page_count=len(pages),
            pages=tuple(pages),
            segments=tuple(segments),
            # OCR can alter a negation, number, unit or symbol. A human must
            # compare it to the original before treating it as reliable proof.
            requires_human_review=any(page.used_ocr for page in pages),
        )

    @staticmethod
    def _decode_text(payload: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8", "cp1252"):
            try:
                return payload.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise DocumentExtractionError("Le fichier texte n'est pas dans un encodage reconnu (UTF-8/Windows-1252).")

    def _extract_pdf_pages(self, payload: bytes) -> list[ExtractedPage]:
        try:
            import fitz  # PyMuPDF
        except ImportError as exc:
            raise DocumentExtractionError("La dépendance PDF requise n’est pas installée sur ce serveur.") from exc

        try:
            document = fitz.open(stream=payload, filetype="pdf")
        except Exception as exc:
            raise DocumentExtractionError("Le fichier PDF est illisible ou invalide.") from exc
        try:
            if document.is_encrypted:
                raise DocumentExtractionError("Le PDF est chiffré ou protégé par mot de passe.")
            if len(document) == 0:
                raise DocumentExtractionError("Le PDF ne contient aucune page.")
            if len(document) > self.max_pages:
                raise DocumentExtractionError(f"PDF trop long: maximum {self.max_pages} pages.")

            pages: list[ExtractedPage] = []
            for page_index, page in enumerate(document):
                text = page.get_text("text") or ""
                if text.strip():
                    pages.append(ExtractedPage(page_number=page_index + 1, text=text, used_ocr=False))
                    continue
                pages.append(
                    ExtractedPage(
                        page_number=page_index + 1,
                        text=self._ocr_pdf_page(page, page_index + 1, fitz),
                        used_ocr=True,
                    )
                )
            return pages
        finally:
            document.close()

    def _ocr_pdf_page(self, page: object, page_number: int, fitz: object) -> str:
        try:
            page_rect = page.rect  # type: ignore[attr-defined]
            estimated_pixels = math.ceil(page_rect.width * self.ocr_render_scale) * math.ceil(
                page_rect.height * self.ocr_render_scale
            )
            if estimated_pixels > self.max_image_pixels:
                raise DocumentExtractionError(
                    f"La page {page_number} dépasse la limite de rendu OCR autorisée."
                )
            pixmap = page.get_pixmap(  # type: ignore[attr-defined]
                matrix=fitz.Matrix(self.ocr_render_scale, self.ocr_render_scale),
                alpha=False,
            )
            if pixmap.width * pixmap.height > self.max_image_pixels:
                raise DocumentExtractionError(
                    f"La page {page_number} dépasse la limite de rendu OCR autorisée."
                )
            image = self._open_checked_image(pixmap.tobytes("png"))
            try:
                return self._ocr_image(image)
            finally:
                image.close()
        except DocumentExtractionError:
            raise
        except Exception as exc:
            raise DocumentExtractionError(
                f"OCR indisponible ou en échec à la page {page_number}; vérifier Tesseract et les langues installées."
            ) from exc

    def _extract_image_text(self, payload: bytes) -> str:
        try:
            image = self._open_checked_image(payload)
            try:
                return self._ocr_image(image)
            finally:
                image.close()
        except DocumentExtractionError:
            raise
        except Exception as exc:
            raise DocumentExtractionError("OCR impossible; vérifier Tesseract et les langues installées.") from exc

    def _ocr_image(self, image: object) -> str:
        try:
            import pytesseract
        except ImportError as exc:
            raise DocumentExtractionError("La dépendance OCR requise n’est pas installée sur ce serveur.") from exc
        try:
            return str(
                pytesseract.image_to_string(
                    image,
                    lang=self.tesseract_languages,
                    timeout=self.ocr_timeout_seconds,
                )
            )
        except Exception as exc:
            raise DocumentExtractionError("OCR impossible; vérifier Tesseract et les langues installées.") from exc

    def _open_checked_image(self, payload: bytes):
        try:
            from PIL import Image
        except ImportError as exc:
            raise DocumentExtractionError("La dépendance image requise n’est pas installée sur ce serveur.") from exc
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                probe = Image.open(BytesIO(payload))
                try:
                    self._assert_image_pixels(probe.width, probe.height)
                    probe.verify()
                finally:
                    probe.close()
                image = Image.open(BytesIO(payload))
                self._assert_image_pixels(image.width, image.height)
                image.load()
                return image
        except DocumentExtractionError:
            raise
        except Exception as exc:
            raise DocumentExtractionError("Le fichier image est illisible ou dépasse les limites de sécurité.") from exc

    def _assert_image_pixels(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0 or width * height > self.max_image_pixels:
            raise DocumentExtractionError("L’image dépasse les limites de pixels autorisées pour l’OCR.")

    def _canonicalize_pages(self, pages: list[ExtractedPage]) -> tuple[str, list[ExtractedSegment]]:
        document_parts: list[str] = []
        segments: list[ExtractedSegment] = []
        current_offset = 0
        for page in pages:
            normalized = self._clean(page.text)
            if not normalized:
                continue
            if document_parts:
                document_parts.append("\n\n")
                current_offset += 2
            page_offset = current_offset
            document_parts.append(normalized)
            current_offset += len(normalized)
            segment_type = "image_ocr" if page.used_ocr else "paragraph"
            for start, end in self._paragraph_ranges(normalized):
                for chunk_start, chunk_end in self._chunk_range(normalized, start, end):
                    segment_text = normalized[chunk_start:chunk_end]
                    if segment_text:
                        segments.append(
                            ExtractedSegment(
                                page_number=page.page_number,
                                segment_type=segment_type,
                                text=segment_text,
                                start_offset=page_offset + chunk_start,
                                end_offset=page_offset + chunk_end,
                            )
                        )
        return "".join(document_parts), segments

    @staticmethod
    def _paragraph_ranges(value: str):
        for match in re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]+)*", value):
            start, end = match.span()
            while start < end and value[start].isspace():
                start += 1
            while end > start and value[end - 1].isspace():
                end -= 1
            if start < end:
                yield start, end

    def _chunk_range(self, value: str, start: int, end: int):
        cursor = start
        while cursor < end:
            while cursor < end and value[cursor].isspace():
                cursor += 1
            if cursor >= end:
                return
            chunk_end = min(cursor + self.segment_max_chars, end)
            if chunk_end < end:
                boundary = value.rfind(" ", cursor + 1, chunk_end + 1)
                if boundary > cursor:
                    chunk_end = boundary
            while chunk_end > cursor and value[chunk_end - 1].isspace():
                chunk_end -= 1
            if chunk_end <= cursor:
                chunk_end = min(cursor + self.segment_max_chars, end)
            yield cursor, chunk_end
            cursor = chunk_end

    @staticmethod
    def _clean(text: str) -> str:
        text = text.replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[\t\f\v]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
