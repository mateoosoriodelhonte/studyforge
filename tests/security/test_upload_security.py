"""Uploads are the largest attack surface in StudyForge. Treat them as hostile.

These tests exist to make the security properties *executable* rather than
aspirational: each one names an attack and asserts it does not work.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from studyforge.documents.storage import DocumentStorage, UnsafeStoragePathError
from studyforge.documents.validation import (
    ALLOWED_EXTENSIONS,
    EmptyUploadError,
    FileTooLargeError,
    UnsupportedFileTypeError,
    UploadError,
    safe_display_filename,
    validate_upload,
)

TEXT = b"A binary search tree stores keys in sorted order so lookups are fast."


def accept(filename: str, content: bytes, content_type: str | None = None) -> None:
    validate_upload(
        filename=filename,
        content=content,
        declared_content_type=content_type,
        max_bytes=1_000_000,
    )


class TestPathTraversal:
    """A filename is attacker-controlled input, never a path component."""

    @pytest.mark.parametrize(
        "hostile",
        [
            "../../etc/passwd",
            "../../../../../../etc/shadow",
            "..\\..\\Windows\\System32\\config\\SAM",
            "/etc/passwd",
            "C:\\Windows\\win.ini",
            "....//....//etc/passwd",
            "notes/../../../secret.txt",
            "./././../../x.txt",
        ],
    )
    def test_directory_components_are_stripped(self, hostile: str) -> None:
        safe = safe_display_filename(hostile)
        assert "/" not in safe
        assert "\\" not in safe
        assert not safe.startswith(".")
        assert ".." not in safe

    def test_a_stored_filename_contains_no_user_input(self, tmp_path: Path) -> None:
        """Traversal is prevented by construction, not by filtering."""
        storage = DocumentStorage(tmp_path / "uploads")
        upload = validate_upload(
            filename="../../evil.txt",
            content=TEXT,
            declared_content_type="text/plain",
            max_bytes=1_000_000,
        )
        stored = storage.store(upload)
        assert "evil" not in stored.stored_filename
        assert stored.stored_filename.endswith(".txt")
        assert stored.path.parent == storage.root

    @pytest.mark.parametrize(
        "escape", ["../outside.txt", "../../etc/passwd", "/etc/passwd", "uploads/../../x"]
    )
    def test_resolving_a_path_outside_the_store_is_refused(
        self, tmp_path: Path, escape: str
    ) -> None:
        storage = DocumentStorage(tmp_path / "uploads")
        storage.root.mkdir(parents=True)
        with pytest.raises(UnsafeStoragePathError):
            storage.resolve(escape)

    def test_a_percent_encoded_traversal_stays_inside_the_store(self, tmp_path: Path) -> None:
        """Storage does no URL decoding, so %2F is an ordinary filename character.

        Decoding belongs to the HTTP layer. Doing it again here would *create*
        a traversal where none existed, which is how double-decoding bugs
        happen; the correct behaviour is to treat the name literally.
        """
        storage = DocumentStorage(tmp_path / "uploads")
        storage.root.mkdir(parents=True)
        assert storage.resolve("..%2Fx.txt").parent == storage.root

    def test_a_symlink_out_of_the_store_is_refused(self, tmp_path: Path) -> None:
        storage = DocumentStorage(tmp_path / "uploads")
        storage.root.mkdir(parents=True)
        secret = tmp_path / "secret.txt"
        secret.write_text("classified")
        (storage.root / "link.txt").symlink_to(secret)
        with pytest.raises(UnsafeStoragePathError):
            storage.read("link.txt")


class TestMaliciousFilenames:
    @pytest.mark.parametrize(
        "hostile",
        [
            "note\x00.txt",
            "note\r\n.txt",
            "note\x1b[31m.txt",
            "note<script>alert(1)</script>.txt",
            "note|rm -rf /.txt",
            "note:stream.txt",
            "?*.txt",
        ],
    )
    def test_dangerous_characters_are_removed(self, hostile: str) -> None:
        safe = safe_display_filename(hostile)
        assert not set(safe) & set('\x00\r\n<>:"/\\|?*')

    @pytest.mark.parametrize("empty", ["", None, "   ", "...", "///", "\x00"])
    def test_an_unusable_filename_becomes_a_placeholder(self, empty: str | None) -> None:
        assert safe_display_filename(empty) == "untitled"

    def test_an_absurdly_long_filename_is_bounded(self) -> None:
        safe = safe_display_filename("x" * 5000 + ".pdf")
        assert len(safe) <= 120
        assert safe.endswith(".pdf"), "the extension must survive truncation"

    def test_unicode_filenames_are_preserved(self) -> None:
        """Safety must not mean mangling legitimate non-English names."""
        assert "講義" in safe_display_filename("講義ノート.txt")


class TestContentTypeSpoofing:
    def test_an_executable_renamed_to_txt_is_rejected(self) -> None:
        with pytest.raises(UnsupportedFileTypeError, match="executable"):
            accept("innocent.txt", b"MZ\x90\x00\x03" + b"\x00" * 100, "text/plain")

    def test_an_elf_binary_renamed_to_md_is_rejected(self) -> None:
        with pytest.raises(UnsupportedFileTypeError, match="executable"):
            accept("readme.md", b"\x7fELF\x02\x01\x01" + b"\x00" * 60, "text/markdown")

    def test_a_shell_script_renamed_to_txt_is_rejected(self) -> None:
        with pytest.raises(UnsupportedFileTypeError, match="script"):
            accept("notes.txt", b"#!/bin/sh\nrm -rf /\n", "text/plain")

    def test_a_zip_renamed_to_pdf_is_rejected(self) -> None:
        with pytest.raises(UnsupportedFileTypeError):
            accept("archive.pdf", b"PK\x03\x04" + b"\x00" * 60, "application/pdf")

    def test_a_gzip_bomb_shaped_file_is_rejected(self) -> None:
        with pytest.raises(UnsupportedFileTypeError, match="archive"):
            accept("notes.txt", b"\x1f\x8b\x08" + b"\x00" * 200, "text/plain")

    def test_a_text_file_that_is_really_a_pdf_is_rejected(self) -> None:
        with pytest.raises(UnsupportedFileTypeError, match=r"binary|does not match"):
            accept("notes.txt", b"%PDF-1.4\n\x00binary", "text/plain")

    def test_a_pdf_extension_with_non_pdf_content_is_rejected(self) -> None:
        with pytest.raises(UnsupportedFileTypeError, match="not a valid PDF"):
            accept("lecture.pdf", TEXT, "application/pdf")

    def test_a_mismatched_declared_content_type_is_rejected(self) -> None:
        with pytest.raises(UnsupportedFileTypeError, match="does not match"):
            accept("notes.txt", TEXT, "image/png")

    def test_binary_smuggled_into_a_text_file_is_rejected(self) -> None:
        with pytest.raises(UnsupportedFileTypeError, match="binary"):
            accept("notes.txt", b"looks fine\x00\x01\x02then binary", "text/plain")


class TestFileTypeAllowList:
    @pytest.mark.parametrize(
        "extension",
        [".exe", ".sh", ".py", ".js", ".html", ".svg", ".zip", ".docx", ".jpg", ".", ""],
    )
    def test_anything_outside_the_allow_list_is_refused(self, extension: str) -> None:
        with pytest.raises(UnsupportedFileTypeError):
            accept(f"file{extension}", TEXT, "text/plain")

    @pytest.mark.parametrize("extension", sorted(ALLOWED_EXTENSIONS))
    def test_every_advertised_format_is_actually_accepted(self, extension: str) -> None:
        content = b"%PDF-1.4\n" + TEXT if extension == ".pdf" else TEXT
        accept(f"notes{extension}", content)

    def test_the_extension_check_is_case_insensitive(self) -> None:
        accept("NOTES.TXT", TEXT, "text/plain")

    def test_a_double_extension_is_judged_on_the_last_one(self) -> None:
        with pytest.raises(UnsupportedFileTypeError):
            accept("notes.txt.exe", TEXT, "text/plain")


class TestSizeLimits:
    def test_an_oversized_upload_is_rejected(self) -> None:
        with pytest.raises(FileTooLargeError, match="over the"):
            validate_upload(
                filename="big.txt",
                content=b"x" * 2001,
                declared_content_type="text/plain",
                max_bytes=2000,
            )

    def test_the_error_names_both_sizes_in_plain_language(self) -> None:
        with pytest.raises(FileTooLargeError) as caught:
            validate_upload(
                filename="big.txt",
                content=b"x" * (5 * 1024 * 1024),
                declared_content_type="text/plain",
                max_bytes=1024 * 1024,
            )
        assert "5 MB" in caught.value.message
        assert "1 MB" in caught.value.message

    def test_an_empty_upload_is_rejected(self) -> None:
        with pytest.raises(EmptyUploadError):
            accept("empty.txt", b"", "text/plain")

    def test_a_file_exactly_at_the_limit_is_accepted(self) -> None:
        validate_upload(
            filename="edge.txt",
            content=b"x" * 100,
            declared_content_type="text/plain",
            max_bytes=100,
        )


class TestErrorDisclosure:
    """Rejection messages are shown to users and must leak nothing."""

    @pytest.mark.parametrize(
        ("filename", "content", "content_type"),
        [
            ("evil.exe", b"MZ", "application/octet-stream"),
            ("big.txt", b"x" * 5000, "text/plain"),
            ("empty.txt", b"", "text/plain"),
            ("fake.pdf", b"nope", "application/pdf"),
        ],
    )
    def test_no_message_leaks_internals(
        self, filename: str, content: bytes, content_type: str
    ) -> None:
        with pytest.raises(UploadError) as caught:
            validate_upload(
                filename=filename,
                content=content,
                declared_content_type=content_type,
                max_bytes=1000,
            )
        message = caught.value.message
        for leak in ("Traceback", "/Users/", "/home/", "site-packages", "pypdf", "0x"):
            assert leak not in message
        assert message[0].isupper() and message.endswith((".", "!"))


class TestStoredFilePermissions:
    def test_stored_files_are_owner_only(self, tmp_path: Path) -> None:
        storage = DocumentStorage(tmp_path / "uploads")
        upload = validate_upload(
            filename="notes.txt",
            content=TEXT,
            declared_content_type="text/plain",
            max_bytes=1_000_000,
        )
        stored = storage.store(upload)
        assert stored.path.stat().st_mode & 0o777 == 0o600

    def test_two_uploads_never_collide(self, tmp_path: Path) -> None:
        storage = DocumentStorage(tmp_path / "uploads")
        names = set()
        for _ in range(50):
            upload = validate_upload(
                filename="same.txt",
                content=TEXT,
                declared_content_type="text/plain",
                max_bytes=1_000_000,
            )
            names.add(storage.store(upload).stored_filename)
        assert len(names) == 50

    def test_deleting_a_missing_file_is_not_an_error(self, tmp_path: Path) -> None:
        storage = DocumentStorage(tmp_path / "uploads")
        storage.root.mkdir(parents=True)
        storage.delete("does-not-exist.txt")
