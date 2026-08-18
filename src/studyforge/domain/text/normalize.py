"""Normalise raw extracted text into something worth chunking.

Text arriving from a PDF or a notes app is hostile to downstream processing in
predictable ways: words split across line breaks by hyphenation, paragraphs hard
wrapped at 72 columns, ligatures, smart quotes, non-breaking spaces, and stray
control characters. Feeding that directly into concept extraction produces
garbage matches and split definitions.

Every function here is pure and **idempotent** -- ``normalize_text`` applied
twice must equal ``normalize_text`` applied once. That property is asserted by
the test suite, because a normaliser that keeps changing its own output cannot
be reasoned about and will silently invalidate stored character offsets.
"""

from __future__ import annotations

import re
import unicodedata

# Characters that carry no meaning but break string matching: zero-width spaces,
# joiners, byte-order marks and soft hyphens.
_INVISIBLE = dict.fromkeys(
    [0x00AD, 0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF],
    None,
)

# Typographic variants folded to their ASCII equivalents. This is deliberate and
# slightly lossy: study material is matched, searched and compared as text, and
# a curly apostrophe that fails to match a straight one is a bug the user would
# experience as "search is broken".
_PUNCTUATION_FOLDS = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "′": "'",
        "″": '"',
        "–": "-",  # en dash
        "—": "-",  # em dash
        "―": "-",
        "−": "-",  # minus sign
        " ": " ",  # non-breaking space
        " ": " ",
        " ": " ",
        " ": " ",
        "\t": " ",
    }
)

# A word broken across a line by hyphenation: "informa-\ntion".
_HYPHEN_LINEBREAK = re.compile(r"(\w)-\n(\w)")

# A single newline between two lines that are clearly one continued sentence.
# Requires the next line to start lowercase, so genuine short lines -- headings,
# list items, "Q:" prompts -- are never glued onto the previous paragraph.
_HARD_WRAP = re.compile(r"(?<=[^\n\s])\n(?=[a-z])")

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACES = re.compile(r"[^\S\n]+")
_TRAILING_SPACE = re.compile(r"[^\S\n]+$", re.MULTILINE)
_LEADING_SPACE = re.compile(r"^[^\S\n]+", re.MULTILINE)
_BLANK_RUN = re.compile(r"\n{3,}")

# A page footer like "12" or "Page 12 of 40" alone on a line -- the single most
# common source of junk "concepts" from PDF extraction.
_PAGE_ARTIFACT = re.compile(
    r"^[^\S\n]*(?:page[^\S\n]+)?\d{1,4}(?:[^\S\n]*/[^\S\n]*\d{1,4}|[^\S\n]+of[^\S\n]+\d{1,4})?[^\S\n]*$",
    re.IGNORECASE | re.MULTILINE,
)


def normalize_text(raw: str) -> str:
    """Clean extracted text without changing what it says.

    The order matters. De-hyphenation runs before hard-wrap joining, because
    joining first would leave a stray hyphen mid-word. Blank-line collapsing runs
    last, once removed page artefacts have left their own empty lines behind.
    """
    if not raw:
        return ""

    text = unicodedata.normalize("NFKC", raw)
    text = text.translate(_INVISIBLE)
    text = text.translate(_PUNCTUATION_FOLDS)
    text = _CONTROL_CHARS.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    text = _HYPHEN_LINEBREAK.sub(r"\1\2", text)
    text = _PAGE_ARTIFACT.sub("", text)
    text = _HARD_WRAP.sub(" ", text)

    text = _SPACES.sub(" ", text)
    text = _TRAILING_SPACE.sub("", text)
    text = _LEADING_SPACE.sub("", text)
    text = _BLANK_RUN.sub("\n\n", text)

    return text.strip()


#: Below this, there is not enough text to generate anything defensible from.
MINIMUM_MEANINGFUL_CHARS = 40

_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


def is_probably_meaningful(text: str, *, minimum_chars: int = MINIMUM_MEANINGFUL_CHARS) -> bool:
    """Whether ``text`` is worth running the rest of the pipeline over.

    Guards the case that matters most in practice: a scanned PDF whose text
    layer yields a handful of stray glyphs. Producing "study material" from that
    would be worse than reporting honestly that nothing could be extracted.
    """
    stripped = text.strip()
    if len(stripped) < minimum_chars:
        return False
    words = _WORD.findall(stripped)
    if len(words) < 5:
        return False
    # Mostly-punctuation output is a classic broken-extraction signature.
    alpha = sum(1 for c in stripped if c.isalpha())
    return alpha / len(stripped) >= 0.5
