"""Where uploaded files live on disk.

Path traversal is prevented *by construction*, not by filtering: stored
filenames are generated from a UUID plus an extension drawn from the validated
allow-list, so no byte of user input ever reaches a path. The resolved path is
then verified to sit inside the uploads directory as a second, independent
check -- belt and braces, because this is the failure mode that turns a notes
app into a remote file read.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from studyforge.documents.validation import ALLOWED_EXTENSIONS, ValidatedUpload
from studyforge.logging_config import log_event

logger = logging.getLogger(__name__)


class UnsafeStoragePathError(Exception):
    """Raised when a resolved path escapes the uploads directory.

    Reaching this should be impossible. It exists so that if it ever becomes
    possible, the result is a loud failure rather than a silent file read.
    """


@dataclass(frozen=True, slots=True)
class StoredUpload:
    """A file written to disk, and the metadata needed to find it again."""

    stored_filename: str
    path: Path
    size_bytes: int
    sha256: str


class DocumentStorage:
    """Reads and writes uploaded documents under a fixed root."""

    def __init__(self, uploads_dir: Path) -> None:
        self._root = uploads_dir.resolve()

    @property
    def root(self) -> Path:
        return self._root

    def store(self, upload: ValidatedUpload) -> StoredUpload:
        """Write a validated upload and return where it went."""
        self._root.mkdir(parents=True, exist_ok=True)
        stored_filename = self._generate_filename(upload.kind.extension)
        path = self.resolve(stored_filename)

        path.write_bytes(upload.content)
        # Owner read/write only. The file is one user's private study material.
        path.chmod(0o600)

        digest = hashlib.sha256(upload.content).hexdigest()
        log_event(
            logger,
            "document_stored",
            stored_filename=stored_filename,
            size_bytes=upload.size_bytes,
            extension=upload.kind.extension,
        )
        return StoredUpload(
            stored_filename=stored_filename,
            path=path,
            size_bytes=upload.size_bytes,
            sha256=digest,
        )

    def resolve(self, stored_filename: str) -> Path:
        """Map an internal filename to its path, refusing anything that escapes.

        Callers only ever pass filenames this class generated. The check is here
        because "only ever" is an assumption, and assumptions about path
        handling are exactly what gets exploited.
        """
        candidate = (self._root / stored_filename).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise UnsafeStoragePathError(
                f"refusing to access {stored_filename!r} outside the store"
            )
        return candidate

    def read(self, stored_filename: str) -> bytes:
        return self.resolve(stored_filename).read_bytes()

    def delete(self, stored_filename: str) -> None:
        """Remove a stored file. Missing files are not an error.

        Deleting a document row whose file has already gone should not fail; the
        desired end state is the same either way.
        """
        try:
            self.resolve(stored_filename).unlink(missing_ok=True)
        except UnsafeStoragePathError:
            logger.warning(
                "refused to delete a path outside the store",
                extra={"event": "unsafe_delete_refused"},
            )
            raise

    @staticmethod
    def _generate_filename(extension: str) -> str:
        """Build a storage name containing no user input at all."""
        if extension not in ALLOWED_EXTENSIONS:  # pragma: no cover - guarded upstream
            raise UnsafeStoragePathError(f"extension {extension!r} is not in the allow-list")
        return f"{uuid.uuid4().hex}{extension}"
