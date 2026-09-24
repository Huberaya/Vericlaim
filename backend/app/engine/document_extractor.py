"""In-memory text extraction for user-supplied copy and scanned documents.

OCR output is only an input to the deterministic claim detector and must be
visually checked against the original, especially for negatives, percentages,
subscripts and decimal separators.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import re

from app.core.config import settings


class DocumentExtractionError(ValueError):
    pass


class DocumentTextExtractor:
    ALLOWED_EXTENSIONS = {".txt", ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"}

    def __init__(self, *, max_bytes: int | None = None, max_pages: int | None = None, max_chars: int | None = None):
        self.max_bytes = max_bytes or settings.max_upload_bytes
        self.max_pages = max_pages or settings.max_pdf_pages
        self.max_chars = max_chars or settings.max_source_chars

    def extract(self, filename: str, content_type: str | None, payload: bytes) -> tuple[str, str]:
        if len(payload) > self.max_bytes:
            raise DocumentExtractionError(f"Fichier trop volumineux: maximum {self.max_bytes} octets.")
        extension = Path(filename or "").suffix.lower()
        if extension not in self.ALLOWED_EXTENSIONS:
            raise DocumentExtractionError("Type de fichier non pris en charge. Utilisez TXT, PDF, PNG, JPG, TIFF ou WEBP.")
        if not payload:
            raise DocumentExtractionError("Le document est vide.")

        if extension == ".txt":
            text = self._decode_text(payload)
            method = "TEXT_FILE"
        elif extension == ".pdf":
            text, method = self._extract_pdf(payload)
        else:
            text = self._extract_image(payload)
            method = "OCR_IMAGE"

        text = self._clean(text)
        if not text:
            raise DocumentExtractionError("Aucun texte exploitable n'a été extrait du document.")
        if len(text) > self.max_chars:
            raise DocumentExtractionError(f"Texte extrait trop long: maximum {self.max_chars} caractères.")
        return text, method

    @staticmethod
    def _decode_text(payload: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8", "cp1252"):
            try:
                return payload.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise DocumentExtractionError("Le fichier texte n'est pas dans un encodage reconnu (UTF-8/Windows-1252).")

    def _extract_pdf(self, payload: bytes) -> tuple[str, str]:
        try:
            import fitz  # PyMuPDF
            import pytesseract
            from PIL import Image
        except ImportError as exc:
            raise DocumentExtractionError("Les dépendances PDF/OCR ne sont pas installées sur ce serveur.") from exc

        try:
            document = fitz.open(stream=payload, filetype="pdf")
        except Exception as exc:
            raise DocumentExtractionError("Le fichier PDF est illisible ou invalide.") from exc
        if len(document) > self.max_pages:
            raise DocumentExtractionError(f"PDF trop long: maximum {self.max_pages} pages.")

        pages: list[str] = []
        used_ocr = False
        for page_index, page in enumerate(document):
            text = page.get_text("text") or ""
            if text.strip():
                pages.append(text)
                continue
            used_ocr = True
            try:
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                image = Image.open(BytesIO(pixmap.tobytes("png")))
                text = pytesseract.image_to_string(
                    image,
                    lang=settings.tesseract_languages,
                    timeout=20,
                )
            except Exception as exc:
                raise DocumentExtractionError(
                    f"OCR indisponible ou en échec à la page {page_index + 1}; vérifier Tesseract et les langues installées."
                ) from exc
            pages.append(text or "")
        return "\n".join(pages), ("PDF_OCR" if used_ocr else "PDF_TEXT")

    @staticmethod
    def _extract_image(payload: bytes) -> str:
        try:
            import pytesseract
            from PIL import Image, UnidentifiedImageError
        except ImportError as exc:
            raise DocumentExtractionError("Les dépendances OCR ne sont pas installées sur ce serveur.") from exc
        try:
            image = Image.open(BytesIO(payload))
            image.verify()
            image = Image.open(BytesIO(payload))
            return pytesseract.image_to_string(image, lang=settings.tesseract_languages, timeout=20)
        except (UnidentifiedImageError, OSError) as exc:
            raise DocumentExtractionError("Le fichier image est illisible ou invalide.") from exc
        except Exception as exc:
            raise DocumentExtractionError("OCR impossible; vérifier Tesseract et les langues installées.") from exc

    @staticmethod
    def _clean(text: str) -> str:
        text = text.replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[\t\f\v]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
