"""Upload validation. Every uploaded byte is treated as hostile.

Three independent checks, because any one alone is bypassable:

1. **Extension** -- cheap, and what the user thinks they uploaded.
2. **Declared content type** -- supplied by the browser, therefore a hint only.
3. **Magic bytes** -- what the file actually is.

A ``.txt`` extension proves nothing, and neither does a ``text/plain`` header;
both are attacker-controlled. The magic-byte check decides, with the other two
rejecting obvious mismatches early and cheaply.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


class UploadError(Exception):
    """Base class for a refused upload.

    The message is written for the person who uploaded the file, not for a log:
    it is rendered directly in the UI, so it must never leak a filesystem path,
    a library name or a stack frame.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class FileTooLargeError(UploadError):
    pass


class UnsupportedFileTypeError(UploadError):
    pass


class EmptyUploadError(UploadError):
    pass


@dataclass(frozen=True, slots=True)
class FileKind:
    """A file type StudyForge accepts."""

    extension: str
    content_types: frozenset[str]
    #: Byte prefixes identifying the format. Empty means "cannot be sniffed",
    #: which is true of plain text and is handled by a decodability check.
    magic: tuple[bytes, ...] = ()
    is_pdf: bool = False


PDF = FileKind(
    extension=".pdf",
    content_types=frozenset({"application/pdf", "application/x-pdf"}),
    magic=(b"%PDF-",),
    is_pdf=True,
)
PLAIN_TEXT = FileKind(
    extension=".txt",
    content_types=frozenset({"text/plain", "application/octet-stream", ""}),
)
MARKDOWN = FileKind(
    extension=".md",
    content_types=frozenset(
        {"text/markdown", "text/x-markdown", "text/plain", "application/octet-stream", ""}
    ),
)

#: The complete allow-list. Anything not here is refused; there is no
#: "unknown but probably fine" path.
ALLOWED_KINDS: tuple[FileKind, ...] = (PDF, PLAIN_TEXT, MARKDOWN)
ALLOWED_EXTENSIONS = frozenset(kind.extension for kind in ALLOWED_KINDS)

#: Signatures never accepted regardless of extension: executables and archives
#: have no business in a notes app, and their presence means the name is a lie.
_FORBIDDEN_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"MZ", "a Windows executable"),
    (b"\x7fELF", "a Linux executable"),
    (b"\xca\xfe\xba\xbe", "a compiled binary"),
    (b"\xcf\xfa\xed\xfe", "a macOS executable"),
    (b"PK\x03\x04", "a zip archive"),
    (b"\x1f\x8b", "a gzip archive"),
    (b"Rar!", "a RAR archive"),
    (b"#!", "a script"),
)

_UNSAFE_FILENAME_CHARS = re.compile(r"[\x00-\x1f\x7f<>:\"/\\|?*]")
_MAX_DISPLAY_FILENAME = 120


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    """An upload that has passed every check."""

    content: bytes
    kind: FileKind
    display_filename: str
    size_bytes: int

    @property
    def is_pdf(self) -> bool:
        return self.kind.is_pdf


def validate_upload(
    *,
    filename: str | None,
    content: bytes,
    declared_content_type: str | None,
    max_bytes: int,
) -> ValidatedUpload:
    """Validate an uploaded file, or raise :class:`UploadError`.

    ``content`` is the fully-read body. The caller must already have enforced
    the size limit *while streaming*, so a hostile upload is never buffered
    whole; the size check here is the backstop for that.
    """
    if not content:
        raise EmptyUploadError("That file is empty. Please choose a file with some content in it.")

    if len(content) > max_bytes:
        raise FileTooLargeError(
            f"That file is {_megabytes(len(content))} MB, which is over the "
            f"{_megabytes(max_bytes)} MB limit."
        )

    display = safe_display_filename(filename)
    extension = _extension_of(display)

    kind = next((k for k in ALLOWED_KINDS if k.extension == extension), None)
    if kind is None:
        readable = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise UnsupportedFileTypeError(
            f"StudyForge cannot read {extension or 'that kind of'} files. "
            f"Supported formats are: {readable}."
        )

    _reject_forbidden_content(content)

    declared = (declared_content_type or "").split(";")[0].strip().lower()
    if declared and declared not in kind.content_types:
        raise UnsupportedFileTypeError(
            f"That file says it is {declared}, which does not match a {extension} file."
        )

    if kind.magic and not content.startswith(kind.magic):
        raise UnsupportedFileTypeError(
            f"That file is named {extension} but its contents are not a valid "
            f"{extension.lstrip('.').upper()} file."
        )

    if not kind.magic:
        _require_decodable_text(content, extension)

    return ValidatedUpload(
        content=content, kind=kind, display_filename=display, size_bytes=len(content)
    )


def _reject_forbidden_content(content: bytes) -> None:
    """Refuse executables and archives whatever the extension claims."""
    for signature, description in _FORBIDDEN_MAGIC:
        if content.startswith(signature):
            raise UnsupportedFileTypeError(
                f"That file appears to be {description}, not a document. "
                "StudyForge only accepts notes and PDFs."
            )


def _require_decodable_text(content: bytes, extension: str) -> None:
    """Plain text has no magic bytes, so prove it is text by decoding it.

    Embedded NUL bytes are rejected outright: no genuine text file contains one,
    and they are a classic way to smuggle binary content past a naive check.
    """
    if b"\x00" in content:
        raise UnsupportedFileTypeError(
            f"That file is named {extension} but contains binary data, not text."
        )
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            content.decode("latin-1")
        except UnicodeDecodeError as error:  # pragma: no cover - latin-1 maps every byte
            raise UnsupportedFileTypeError(
                f"That {extension} file is not in a text encoding StudyForge can read."
            ) from error


def safe_display_filename(filename: str | None) -> str:
    """Reduce a user-supplied filename to something safe to store and show.

    This value is **metadata only**. It is never used to build a path -- stored
    filenames are generated internally (see :mod:`studyforge.documents.storage`)
    -- so this function's job is limited to making the name safe to display and
    to read an extension from.

    Strips directory components under both separators (defeating
    ``../../etc/passwd`` and Windows-style paths), removes control and NUL
    characters, and bounds the length while preserving the extension.
    """
    if not filename:
        return "untitled"

    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    name = unicodedata.normalize("NFKC", name)
    name = _UNSAFE_FILENAME_CHARS.sub("", name)
    name = name.strip().strip(".")

    if not name:
        return "untitled"

    if len(name) > _MAX_DISPLAY_FILENAME:
        stem, _, extension = name.rpartition(".")
        if extension and len(extension) <= 10:
            keep = _MAX_DISPLAY_FILENAME - len(extension) - 1
            name = f"{stem[:keep]}.{extension}"
        else:
            name = name[:_MAX_DISPLAY_FILENAME]
    return name


def _extension_of(filename: str) -> str:
    _, dot, extension = filename.rpartition(".")
    return f".{extension.lower()}" if dot else ""


def _megabytes(value: int) -> str:
    return f"{value / (1024 * 1024):.1f}".rstrip("0").rstrip(".")
