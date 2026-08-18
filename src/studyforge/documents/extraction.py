"""Turn uploaded bytes into text.

The one rule this module enforces for the rest of the application: **no pypdf
object escapes**. Callers get an :class:`ExtractedDocument` -- plain text,
page count, an outcome -- so replacing the PDF backend later touches this file
and nothing else.

Why pypdf
---------
It is pure Python with no C dependency, so ``uv sync`` works everywhere without
a toolchain, and it is BSD-licensed. PyMuPDF is considerably faster but is
AGPL-licensed, which would force this MIT project's licence to change; that is
not a trade worth making to extract text from lecture notes.

Why no OCR
----------
OCR means a large binary dependency, slow processing, and output quality that
varies enormously. Rather than silently produce bad text from a scan,
StudyForge detects that a PDF has no usable text layer and says so, explicitly,
as :attr:`ExtractionOutcome.NO_TEXT`. A wrong flashcard is worse than no
flashcard.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass

from studyforge.documents.validation import ValidatedUpload
from studyforge.domain.text.normalize import is_probably_meaningful, normalize_text
from studyforge.logging_config import log_event

logger = logging.getLogger(__name__)

#: Refuse to open a PDF claiming more pages than any real set of notes has.
#: Guards against a small file that expands into an enormous page tree.
MAX_PDF_PAGES = 2_000


class ExtractionOutcome(enum.StrEnum):
    """How extraction went.

    ``NO_TEXT`` is deliberately distinct from ``FAILED``. A scanned PDF is not a
    broken file, and the user needs to be told specifically that it looks
    scanned and that OCR is not enabled -- not shown a generic error.
    """

    SUCCESS = "success"
    NO_TEXT = "no_text"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    """The result of extraction. Contains no library objects."""

    outcome: ExtractionOutcome
    text: str = ""
    page_count: int | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.outcome is ExtractionOutcome.SUCCESS

    @property
    def char_count(self) -> int:
        return len(self.text)


#: Shown verbatim to the user. Plain language, no jargon, and it says what to
#: do next rather than merely reporting failure.
SCANNED_PDF_MESSAGE = (
    "This PDF has no text layer, which usually means it is a scan or a set of "
    "photographs. StudyForge does not run OCR, so it cannot read the words. "
    "Try exporting the original document as a PDF with selectable text, or "
    "paste the text in directly."
)


def extract_text(upload: ValidatedUpload) -> ExtractedDocument:
    """Extract normalised text from a validated upload."""
    if upload.is_pdf:
        return _extract_pdf(upload.content)
    return _extract_plain_text(upload.content)


def _extract_plain_text(content: bytes) -> ExtractedDocument:
    """Decode a text file. Validation has already proved it is decodable."""
    try:
        raw = content.decode("utf-8")
    except UnicodeDecodeError:
        # Latin-1 maps every byte, so this cannot fail; it is the lossy but
        # never-crashing fallback for files with an unknown legacy encoding.
        raw = content.decode("latin-1")

    text = normalize_text(raw)
    if not is_probably_meaningful(text):
        return ExtractedDocument(
            outcome=ExtractionOutcome.NO_TEXT,
            text=text,
            error="This file does not contain enough readable text to study from.",
        )
    return ExtractedDocument(outcome=ExtractionOutcome.SUCCESS, text=text)


def _extract_pdf(content: bytes) -> ExtractedDocument:
    """Pull the text layer out of a PDF.

    Every failure mode is caught and converted into an outcome. A corrupt or
    encrypted PDF is an ordinary thing for a user to have, not an exception the
    application should propagate.
    """
    import io

    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
    except (PdfReadError, ValueError, OSError) as error:
        log_event(
            logger,
            "extraction_failed",
            level=logging.WARNING,
            reason="unreadable_pdf",
            detail=str(error),
        )
        return ExtractedDocument(
            outcome=ExtractionOutcome.FAILED,
            error="This PDF could not be opened. It may be corrupted or incomplete.",
        )

    if reader.is_encrypted:
        # An empty-password decrypt covers PDFs that are "encrypted" only to set
        # permission flags, which is common for institutional exports.
        try:
            if not reader.decrypt(""):
                raise PdfReadError("password required")
        except (PdfReadError, NotImplementedError, ValueError):
            return ExtractedDocument(
                outcome=ExtractionOutcome.FAILED,
                error=("This PDF is password protected. Remove the password and upload it again."),
            )

    try:
        page_count = len(reader.pages)
    except (PdfReadError, ValueError, OSError):
        return ExtractedDocument(
            outcome=ExtractionOutcome.FAILED,
            error="This PDF could not be opened. It may be corrupted or incomplete.",
        )

    if page_count > MAX_PDF_PAGES:
        return ExtractedDocument(
            outcome=ExtractionOutcome.FAILED,
            page_count=page_count,
            error=(
                f"This PDF has {page_count:,} pages, which is more than StudyForge "
                f"will process at once (the limit is {MAX_PDF_PAGES:,}). "
                "Try splitting it into smaller documents."
            ),
        )

    pages: list[str] = []
    failed_pages = 0
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - a single bad page must not lose the rest
            failed_pages += 1

    if failed_pages:
        log_event(
            logger,
            "extraction_partial",
            level=logging.WARNING,
            failed_pages=failed_pages,
            page_count=page_count,
        )

    text = normalize_text("\n\n".join(pages))

    if not is_probably_meaningful(text):
        log_event(logger, "extraction_no_text", page_count=page_count, char_count=len(text))
        return ExtractedDocument(
            outcome=ExtractionOutcome.NO_TEXT,
            text=text,
            page_count=page_count,
            error=SCANNED_PDF_MESSAGE,
        )

    log_event(
        logger,
        "extraction_completed",
        page_count=page_count,
        char_count=len(text),
        failed_pages=failed_pages,
    )
    return ExtractedDocument(outcome=ExtractionOutcome.SUCCESS, text=text, page_count=page_count)
