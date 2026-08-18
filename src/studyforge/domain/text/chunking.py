"""Split normalised text into semantically bounded chunks.

Chunks are the unit of provenance in StudyForge: a generated flashcard points at
the exact chunk it came from, and the chunk's character offsets locate that
passage in the source document. That makes two properties non-negotiable:

*Determinism* -- identical input must always produce identical chunks, or a
regenerated card would point somewhere else.

*Offset fidelity* -- ``text[chunk.char_start:chunk.char_end]`` must actually be
the chunk's text, or "show me where this came from" silently lies.

Both are asserted by the test suite over every fixture.

Boundaries are respected in priority order: headings first, then paragraphs,
then sentences. Splitting mid-sentence at a character count is the fallback of
last resort, used only when a single sentence exceeds the hard maximum.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Heading detection
# --------------------------------------------------------------------------

#: Markdown ATX headings: ``## Binary Search Trees``
_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(?P<title>\S.*?)\s*#*$")

#: Numbered section headings: ``3.`` / ``3.2`` / ``IV.`` followed by a title.
_NUMBERED_HEADING = re.compile(r"^(?:\d+(?:\.\d+)*\.?|[IVXLC]+\.)\s+(?P<title>[A-Z]\S.*?)[.:]?$")

#: A short line in title case or all caps, with no terminal punctuation.
_BARE_HEADING = re.compile(r"^(?P<title>[A-Z][^.!?]{2,79})$")

#: Setext underlines: a line of ``===`` or ``---`` beneath the title.
_SETEXT_UNDERLINE = re.compile(r"^(={3,}|-{3,})$")

#: How many characters a line may have and still plausibly be a heading.
_MAX_HEADING_CHARS = 80


def _detect_heading(line: str, next_line: str | None) -> str | None:
    """Return the heading title if ``line`` is one, else ``None``."""
    stripped = line.strip()
    if not stripped or len(stripped) > _MAX_HEADING_CHARS:
        return None

    if match := _MARKDOWN_HEADING.match(stripped):
        return match.group("title").strip()

    if next_line is not None and _SETEXT_UNDERLINE.match(next_line.strip()):
        return stripped

    if match := _NUMBERED_HEADING.match(stripped):
        return match.group("title").strip()

    # A bare heading must not look like a sentence: no trailing full stop, and
    # either all caps or title-ish. Requiring no terminal punctuation is what
    # keeps ordinary short sentences out.
    if match := _BARE_HEADING.match(stripped):
        words = stripped.split()
        if len(words) <= 12 and (stripped.isupper() or _is_title_ish(words)):
            return match.group("title").strip()

    return None


def _is_title_ish(words: list[str]) -> bool:
    """Most significant words capitalised, as a title would be."""
    significant = [w for w in words if len(w) > 3]
    if not significant:
        return False
    capitalised = sum(1 for w in significant if w[0].isupper())
    return capitalised / len(significant) >= 0.6


# --------------------------------------------------------------------------
# Sentence splitting
# --------------------------------------------------------------------------

#: Abbreviations whose full stop does not end a sentence.
_ABBREVIATIONS = frozenset(
    {
        "e.g",
        "i.e",
        "etc",
        "cf",
        "vs",
        "al",
        "approx",
        "fig",
        "eq",
        "ch",
        "sec",
        "no",
        "vol",
        "pp",
        "ed",
        "st",
        "mt",
        "dr",
        "mr",
        "mrs",
        "ms",
        "prof",
        "inc",
        "ltd",
        "co",
        "jr",
        "sr",
        "ca",
        "resp",
    }
)

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])[\"')\]]*[^\S\n]+")
_LINE_BREAK = re.compile(r"\n+")


def split_sentences(text: str) -> list[str]:
    """Split into sentences, keeping abbreviations and decimals intact.

    A regex splitter rather than an NLP dependency: study notes are ordinary
    prose, the failure mode is a slightly odd chunk boundary rather than a wrong
    answer, and a deterministic 30-line function is far easier to reason about
    than a statistical model.
    """
    if not text.strip():
        return []

    sentences: list[str] = []
    # A newline in normalised text is already a structural break -- a heading, a
    # list item, a paragraph. Splitting on it first stops an unpunctuated
    # heading being glued onto the sentence that follows it, which would hide
    # every definition that opens a section.
    for line in _LINE_BREAK.split(text.strip()):
        sentences.extend(_split_line(line))
    return sentences


def _split_line(line: str) -> list[str]:
    if not line.strip():
        return []

    sentences: list[str] = []
    buffer = ""
    for piece in _SENTENCE_BOUNDARY.split(line.strip()):
        candidate = f"{buffer} {piece}".strip() if buffer else piece
        if _ends_mid_sentence(candidate):
            buffer = candidate
            continue
        sentences.append(candidate)
        buffer = ""

    if buffer:
        sentences.append(buffer)
    return [s for s in sentences if s.strip()]


def _ends_mid_sentence(candidate: str) -> bool:
    """Whether a trailing full stop belongs to an abbreviation or a number."""
    if not candidate.endswith("."):
        return False
    tail = candidate[:-1].split()[-1] if candidate[:-1].split() else ""
    lowered = tail.lower().lstrip("(\"'")
    if lowered in _ABBREVIATIONS:
        return True
    # A single initial: "J." in "J. Smith".
    return len(lowered) == 1 and lowered.isalpha()


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Chunk:
    """One semantically bounded passage, located in its source text."""

    ordinal: int
    text: str
    char_start: int
    char_end: int
    heading: str | None = None

    @property
    def char_count(self) -> int:
        return len(self.text)


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """Chunk sizing policy.

    ``target_chars`` is what the chunker aims for; ``max_chars`` is a hard
    ceiling it will split a sentence to respect. ``overlap_chars`` repeats the
    tail of the previous chunk so a definition straddling a boundary still
    appears whole somewhere.
    """

    target_chars: int = 900
    max_chars: int = 1400
    min_chars: int = 120
    overlap_chars: int = 120

    def __post_init__(self) -> None:
        if self.target_chars < 1:
            raise ValueError("target_chars must be positive")
        if self.max_chars < self.target_chars:
            raise ValueError("max_chars must be at least target_chars")
        if self.overlap_chars < 0:
            raise ValueError("overlap_chars cannot be negative")
        if self.overlap_chars >= self.target_chars:
            raise ValueError("overlap_chars must be smaller than target_chars")
        if self.min_chars < 0:
            raise ValueError("min_chars cannot be negative")


@dataclass
class _Block:
    """A paragraph or heading, with its span in the source text."""

    text: str
    start: int
    end: int
    heading: str | None = None
    is_heading: bool = False


@dataclass
class _Accumulator:
    """Blocks gathered so far for the chunk currently being built."""

    parts: list[tuple[str, int, int]] = field(default_factory=list)
    heading: str | None = None

    @property
    def size(self) -> int:
        return sum(len(text) for text, _, _ in self.parts) + max(0, len(self.parts) - 1) * 2

    @property
    def is_empty(self) -> bool:
        return not self.parts


def chunk_text(
    text: str, config: ChunkingConfig | None = None, *, start_ordinal: int = 0
) -> list[Chunk]:
    """Split ``text`` into chunks, respecting semantic boundaries.

    ``text`` is expected to be already normalised. Offsets are relative to the
    string passed in.
    """
    config = config or ChunkingConfig()
    if not text.strip():
        return []

    blocks = _parse_blocks(text)
    chunks: list[Chunk] = []
    accumulator = _Accumulator()
    ordinal = start_ordinal

    def flush() -> None:
        nonlocal accumulator, ordinal
        if accumulator.is_empty:
            return
        chunk_body = "\n\n".join(part for part, _, _ in accumulator.parts)
        chunks.append(
            Chunk(
                ordinal=ordinal,
                text=chunk_body,
                char_start=accumulator.parts[0][1],
                char_end=accumulator.parts[-1][2],
                heading=accumulator.heading,
            )
        )
        ordinal += 1
        accumulator = _Accumulator(heading=accumulator.heading)

    for block in blocks:
        if block.is_heading:
            # A heading starts a new chunk: material under different headings
            # is about different things.
            flush()
            accumulator.heading = block.heading
            continue

        for piece, start, end in _fit_block(block, config):
            projected = accumulator.size + (2 if accumulator.parts else 0) + len(piece)
            if accumulator.parts and projected > config.target_chars:
                flush()
                _seed_overlap(accumulator, chunks, config)
            accumulator.parts.append((piece, start, end))
            if accumulator.size >= config.target_chars:
                flush()
                _seed_overlap(accumulator, chunks, config)

    flush()
    return _merge_runt(chunks, config)


def _seed_overlap(accumulator: _Accumulator, chunks: list[Chunk], config: ChunkingConfig) -> None:
    """Carry the tail of the previous chunk into the next one.

    Overlap is included in the chunk *text* but the chunk's ``char_start`` still
    points at the overlapped region, so offsets remain truthful.
    """
    if not config.overlap_chars or not chunks:
        return
    previous = chunks[-1]
    sentences = split_sentences(previous.text)
    if not sentences:
        return

    tail: list[str] = []
    size = 0
    for sentence in reversed(sentences):
        if size + len(sentence) > config.overlap_chars and tail:
            break
        tail.insert(0, sentence)
        size += len(sentence) + 1

    if not tail or len(tail) == len(sentences):
        # Never overlap the entire previous chunk; that would duplicate it.
        return
    overlap_text = " ".join(tail)
    accumulator.parts.append(
        (overlap_text, previous.char_end - len(overlap_text), previous.char_end)
    )


def _fit_block(block: _Block, config: ChunkingConfig) -> list[tuple[str, int, int]]:
    """Break a paragraph down until every piece fits under ``max_chars``."""
    if len(block.text) <= config.max_chars:
        return [(block.text, block.start, block.end)]

    pieces: list[tuple[str, int, int]] = []
    cursor = block.start
    for sentence in split_sentences(block.text):
        located = block.text.find(sentence, cursor - block.start)
        start = block.start + (located if located >= 0 else 0)
        end = start + len(sentence)
        cursor = end
        if len(sentence) <= config.max_chars:
            pieces.append((sentence, start, end))
        else:
            # Last resort: a single sentence longer than the hard maximum.
            pieces.extend(_hard_split(sentence, start, config.max_chars))
    return pieces or [(block.text, block.start, block.end)]


def _hard_split(text: str, start: int, limit: int) -> list[tuple[str, int, int]]:
    """Split at whitespace nearest the limit, or mid-token if there is none."""
    pieces: list[tuple[str, int, int]] = []
    offset = 0
    while offset < len(text):
        window = text[offset : offset + limit]
        if offset + limit < len(text):
            breakpoint_ = window.rfind(" ")
            if breakpoint_ <= 0:
                breakpoint_ = limit
        else:
            breakpoint_ = len(window)
        piece = text[offset : offset + breakpoint_].strip()
        if piece:
            pieces.append((piece, start + offset, start + offset + breakpoint_))
        offset += breakpoint_ or limit
    return pieces


def _merge_runt(chunks: list[Chunk], config: ChunkingConfig) -> list[Chunk]:
    """Fold a too-small trailing chunk into its predecessor.

    A 15-character final chunk is not a passage; it is a fragment that would
    generate a useless flashcard. Only merged when the result stays under the
    hard maximum and both share a heading.
    """
    if len(chunks) < 2:
        return chunks
    last, previous = chunks[-1], chunks[-2]
    if last.char_count >= config.min_chars:
        return chunks
    combined = f"{previous.text}\n\n{last.text}"
    if len(combined) > config.max_chars or previous.heading != last.heading:
        return chunks
    merged = Chunk(
        ordinal=previous.ordinal,
        text=combined,
        char_start=previous.char_start,
        char_end=last.char_end,
        heading=previous.heading,
    )
    return [*chunks[:-2], merged]


def _parse_blocks(text: str) -> list[_Block]:
    """Split text into heading and paragraph blocks, tracking offsets."""
    blocks: list[_Block] = []
    lines = text.split("\n")
    offsets: list[int] = []
    position = 0
    for line in lines:
        offsets.append(position)
        position += len(line) + 1

    paragraph: list[tuple[str, int]] = []
    current_heading: str | None = None
    index = 0

    def flush_paragraph() -> None:
        if not paragraph:
            return
        body = " ".join(line for line, _ in paragraph).strip()
        if body:
            start = paragraph[0][1]
            last_line, last_start = paragraph[-1]
            blocks.append(
                _Block(
                    text=body,
                    start=start,
                    end=last_start + len(last_line),
                    heading=current_heading,
                )
            )
        paragraph.clear()

    while index < len(lines):
        line = lines[index]
        next_line = lines[index + 1] if index + 1 < len(lines) else None

        if not line.strip():
            flush_paragraph()
            index += 1
            continue

        heading = _detect_heading(line, next_line)
        if heading is not None:
            flush_paragraph()
            current_heading = heading
            blocks.append(
                _Block(
                    text=heading,
                    start=offsets[index],
                    end=offsets[index] + len(line),
                    heading=heading,
                    is_heading=True,
                )
            )
            # Consume a setext underline so it does not become a paragraph.
            if next_line is not None and _SETEXT_UNDERLINE.match(next_line.strip()):
                index += 1
            index += 1
            continue

        paragraph.append((line, offsets[index]))
        index += 1

    flush_paragraph()
    return blocks
