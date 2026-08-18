"""Minimal PDF builders for tests.

Constructing PDFs by hand rather than committing binary fixtures keeps the test
data readable and diff-able, and lets a test express exactly the pathology it
is about ("a PDF with pages but no text layer") in one line.
"""

from __future__ import annotations


def build_pdf(pages: list[str]) -> bytes:
    """A minimal but genuinely valid PDF with one text object per page."""
    objects: list[bytes] = []
    page_count = max(1, len(pages))
    kids = " ".join(f"{4 + i * 2} 0 R" for i in range(page_count))

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    for text in pages or [""]:
        stream = _text_stream(text)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents "
            + str(len(objects) + 2).encode()
            + b" 0 R >>"
        )
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )

    return _assemble(objects)


def build_pdf_without_text_layer(page_count: int = 2) -> bytes:
    """A structurally valid PDF whose pages contain no text at all.

    This is what a scanned document looks like to a text extractor, and the case
    StudyForge must report honestly instead of generating junk from.
    """
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{' '.join(f'{3 + i} 0 R' for i in range(page_count))}] "
        f"/Count {page_count} >>".encode(),
    ]
    objects.extend(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << >> >>"
        for _ in range(page_count)
    )
    return _assemble(objects)


def _text_stream(text: str) -> bytes:
    lines = text.split("\n") or [""]
    body = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
    for index, line in enumerate(lines):
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        body.append(f"({escaped}) Tj" if index == 0 else f"T* ({escaped}) Tj")
    body.append("ET")
    return "\n".join(body).encode("latin-1", errors="replace")


def _assemble(objects: list[bytes]) -> bytes:
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)
