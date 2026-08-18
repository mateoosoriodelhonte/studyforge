"""Text processing: normalisation and semantic chunking.

Pure functions over strings. Nothing here knows about PDFs, databases or HTTP,
which is what lets the whole pipeline be tested with string literals.
"""

from studyforge.domain.text.chunking import (
    Chunk,
    ChunkingConfig,
    chunk_text,
    split_sentences,
)
from studyforge.domain.text.normalize import (
    is_probably_meaningful,
    normalize_text,
)

__all__ = [
    "Chunk",
    "ChunkingConfig",
    "chunk_text",
    "is_probably_meaningful",
    "normalize_text",
    "split_sentences",
]
