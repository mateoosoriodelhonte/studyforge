"""Document ingestion: validation, storage and text extraction.

The boundary rule for this package: **no PDF library object escapes it**.
Callers receive plain dataclasses of text and metadata, so swapping the
extraction backend never ripples into services, routes or templates.
"""

from studyforge.documents.extraction import (
    ExtractedDocument,
    ExtractionOutcome,
    extract_text,
)
from studyforge.documents.storage import DocumentStorage, StoredUpload
from studyforge.documents.validation import (
    ALLOWED_EXTENSIONS,
    EmptyUploadError,
    FileTooLargeError,
    UnsupportedFileTypeError,
    UploadError,
    ValidatedUpload,
    safe_display_filename,
    validate_upload,
)

__all__ = [
    "ALLOWED_EXTENSIONS",
    "DocumentStorage",
    "EmptyUploadError",
    "ExtractedDocument",
    "ExtractionOutcome",
    "FileTooLargeError",
    "StoredUpload",
    "UnsupportedFileTypeError",
    "UploadError",
    "ValidatedUpload",
    "extract_text",
    "safe_display_filename",
    "validate_upload",
]
