"""Non-parser validation for untrusted uploaded bytes.

This module deliberately performs only bounded signature/encoding checks. It
is not a substitute for the malware scanner and it never invokes PDF, image or
OCR libraries before the scanner has accepted the bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath
import hashlib
import re


class DocumentSecurityValidationError(ValueError):
    """The bytes or the supplied metadata cannot enter quarantine safely."""


CONTENT_TYPE_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
}


@dataclass(frozen=True)
class InspectedDocument:
    source_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    #: Nombre de pages, quand il est connaissable **sans rendre** le document (PDF via
    #: sa table de pages, image = 1). ``None`` quand seul le texte peut le dire. C21 en
    #: a besoin : le quota de pages OCR décidait sur « 1 page » alors qu'un scan de 300
    #: pages en consomme 300.
    page_count: int | None = None


def normalize_content_type(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def sanitize_filename(value: str) -> str:
    candidate = value.strip()
    if not candidate or len(candidate) > 500:
        raise DocumentSecurityValidationError("Le nom de fichier est invalide.")
    if "\x00" in candidate or "/" in candidate or "\\" in candidate:
        raise DocumentSecurityValidationError("Le nom de fichier ne doit pas contenir de chemin.")
    # The same display name is later used in a signed Content-Disposition
    # response. Reject quotes rather than giving user input any opportunity to
    # alter that HTTP header's filename parameter.
    if '"' in candidate:
        raise DocumentSecurityValidationError("Le nom de fichier ne doit pas contenir de guillemet.")
    # PurePath handles a few edge cases such as a bare dot while preserving the
    # display name supplied by the user; no name is ever used as a storage key.
    if PurePath(candidate).name != candidate or candidate in {".", ".."}:
        raise DocumentSecurityValidationError("Le nom de fichier est invalide.")
    if any(ord(character) < 32 for character in candidate):
        raise DocumentSecurityValidationError("Le nom de fichier contient un caractère de contrôle.")
    return candidate


def expected_content_type(filename: str) -> str:
    suffix = PurePath(filename).suffix.lower()
    content_type = CONTENT_TYPE_BY_EXTENSION.get(suffix)
    if content_type is None:
        raise DocumentSecurityValidationError(
            "Type de fichier non autorisé. Utilisez PDF, TXT, PNG, JPG, TIFF ou WEBP."
        )
    return content_type


def detect_content_type(payload: bytes, *, filename: str) -> str:
    if payload.startswith(b"%PDF-"):
        return "application/pdf"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if payload.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"

    # Text is deliberately narrow: accepting arbitrary Windows-1252 bytes
    # would make binary payloads look like text. UTF-8/BOM is sufficient for a
    # secure persisted-import path; the legacy decoder remains compatibility
    # code after this boundary has accepted a text file.
    if PurePath(filename).suffix.lower() == ".txt":
        try:
            decoded = payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise DocumentSecurityValidationError("Les fichiers TXT persistés doivent être encodés en UTF-8.") from exc
        if "\x00" in decoded or not decoded.strip():
            raise DocumentSecurityValidationError("Le fichier texte est vide ou contient des octets interdits.")
        return "text/plain"

    raise DocumentSecurityValidationError("La signature réelle du fichier ne correspond pas à un format autorisé.")


def inspect_document_bytes(
    payload: bytes,
    *,
    filename: str,
    declared_content_type: str | None,
    max_size_bytes: int,
) -> InspectedDocument:
    safe_filename = sanitize_filename(filename)
    if not payload:
        raise DocumentSecurityValidationError("Le fichier est vide.")
    if len(payload) > max_size_bytes:
        raise DocumentSecurityValidationError(f"Fichier trop volumineux: maximum {max_size_bytes} octets.")
    expected = expected_content_type(safe_filename)
    actual = detect_content_type(payload, filename=safe_filename)
    declared = normalize_content_type(declared_content_type)
    if not declared:
        raise DocumentSecurityValidationError("Le type MIME déclaré est obligatoire.")
    if declared == "image/jpg":
        declared = "image/jpeg"
    if declared != expected or actual != expected:
        raise DocumentSecurityValidationError("Le type MIME, l’extension et la signature du fichier doivent correspondre.")
    return InspectedDocument(
        source_filename=safe_filename,
        content_type=actual,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        page_count=count_pages_without_rendering(payload, content_type=actual),
    )


def count_pages_without_rendering(payload: bytes, *, content_type: str) -> int | None:
    """Compte les pages sans rendre une seule image.

    Un PDF : sa table de pages suffit (aucun rendu, aucun OCR). Une image : une page.
    Tout autre format : ``None`` — on ne devine pas, et l'appelant décide quoi faire
    d'une estimation inconnue plutôt que de la traiter comme certaine.
    """

    if content_type == "application/pdf":
        try:
            import fitz

            document = fitz.open(stream=payload, filetype="pdf")
            try:
                # Un PDF sans page (0) n'est pas un compte : c'est un document que
                # l'extraction refusera. Le compter « zéro page » ferait échouer la
                # contrainte de base et ferait croire à un import gratuit.
                return int(document.page_count) or None
            finally:
                document.close()
        except Exception:  # noqa: BLE001 - un PDF illisible est refusé juste après
            return None
    if content_type in {"image/png", "image/jpeg", "image/tiff", "image/webp"}:
        return 1
    return None


def is_sha256(value: str) -> bool:
    return bool(re.fullmatch(r"[a-fA-F0-9]{64}", value))
