"""Find the concepts worth learning in a piece of text, without any AI.

What this is
------------
Pattern matching and term statistics. It looks for the textual shapes that
authors use when they are defining something -- "X is a ...", glossary dashes,
headings, terms that recur across a document -- and ranks what it finds by how
much evidence supported it.

What this is not
----------------
Comprehension. It does not know what a binary tree *is*; it knows that the
document said "A binary tree is ..." in a shape that usually means a definition.
That distinction is why extracted concepts surface in the UI as *candidates the
learner can edit or discard*, never as authoritative, and why every candidate
carries the evidence it was derived from.

When an AI provider is configured it can improve on this. The deterministic path
remains the default and stays fully functional on its own.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from studyforge.domain.text.chunking import split_sentences

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

#: Words that must never be the head of a concept. Deliberately small: an
#: aggressive stoplist would eat legitimate technical terms.
STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "am",
        "and",
        "or",
        "but",
        "nor",
        "for",
        "so",
        "yet",
        "if",
        "then",
        "than",
        "as",
        "at",
        "by",
        "from",
        "in",
        "into",
        "of",
        "on",
        "onto",
        "to",
        "with",
        "without",
        "within",
        "about",
        "above",
        "below",
        "over",
        "under",
        "again",
        "further",
        "once",
        "here",
        "there",
        "when",
        "where",
        "why",
        "how",
        "all",
        "any",
        "both",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "not",
        "only",
        "own",
        "same",
        "too",
        "very",
        "can",
        "will",
        "just",
        "should",
        "now",
        "we",
        "you",
        "they",
        "he",
        "she",
        "i",
        "me",
        "my",
        "our",
        "your",
        "their",
        "them",
        "us",
        "him",
        "her",
        "his",
        "hers",
        "theirs",
        "what",
        "which",
        "who",
        "whom",
        "do",
        "does",
        "did",
        "done",
        "have",
        "has",
        "had",
        "having",
        "would",
        "could",
        "may",
        "might",
        "must",
        "shall",
        "also",
        "however",
        "therefore",
        "thus",
        "hence",
        "because",
        "while",
        "during",
        "before",
        "after",
        "example",
        "examples",
        "following",
        "above-mentioned",
        "figure",
        "table",
        "chapter",
        "section",
        "note",
        "notes",
        "page",
        "pages",
        "see",
        "also",
        "e.g",
        "i.e",
        "etc",
        "via",
        "per",
    ]
)

#: Verbs and connectives that signal a definition follows.
_DEFINITION_VERBS = (
    r"(?:is\s+defined\s+as|is\s+known\s+as|is\s+called|consists?\s+of|refers?\s+to"
    r"|denotes?|describes?|represents?|means|is|are|was|were)"
)

#: "A binary search tree is a rooted binary tree that ..." or
#: "Stability is the number of days ...". The leading article is captured
#: separately because it changes how strict we can be about the term.
_DEFINITION_SENTENCE = re.compile(
    r"^(?P<article>An?|The)?\s*"
    r"(?P<term>[\w'\-]+(?:\s+[\w'\-]+){0,4}?)\s+"
    + _DEFINITION_VERBS
    + r"\s+(?P<definition>.{15,})$",
    re.IGNORECASE,
)

#: "Stability: the number of days until recall decays to 90%."
_COLON_DEFINITION = re.compile(
    r"^(?P<term>[A-Z][\w'\-]*(?:\s+[\w'\-]+){0,4})\s*:\s+(?P<definition>\S.{14,})$"
)

#: "Stability - the number of days ..." (a glossary line, not prose).
_DASH_DEFINITION = re.compile(
    r"^(?P<term>[A-Z][\w'\-]*(?:\s+[\w'\-]+){0,4})\s+[-]{1,2}\s+(?P<definition>\S.{14,})$"
)

#: A capitalised multi-word phrase, the usual shape of a technical term.
_TERM_PHRASE = re.compile(r"\b[A-Z][\w'\-]*(?:\s+[A-Z][\w'\-]*){0,3}\b")

#: An acronym: 2-6 capitals, optionally with digits. "AVL", "BST", "O(1)" is not.
_ACRONYM = re.compile(r"\b[A-Z]{2,6}[0-9]{0,2}\b")

_WHITESPACE = re.compile(r"\s+")

#: Punctuation stripped from the ends of a concept name.
_NAME_TRIM_CHARS = "-:;,.()[]\"'" + "\u2013\u2014"  # en dash, em dash


class Method:
    """Extraction evidence, mirroring ``models.ExtractionMethod`` values.

    Kept as plain strings so the domain layer does not import the ORM's enums.
    """

    DEFINITION = "definition"
    GLOSSARY = "glossary"
    HEADING = "heading"
    FREQUENCY = "frequency"


#: Confidence weights per evidence type. An explicit definition sentence is far
#: stronger evidence than a term simply appearing often.
_METHOD_SCORE = {
    Method.DEFINITION: 1.0,
    Method.GLOSSARY: 0.92,
    Method.HEADING: 0.62,
    Method.FREQUENCY: 0.40,
}


@dataclass(frozen=True, slots=True)
class ConceptCandidate:
    """A concept found in the text, with the evidence that produced it."""

    name: str
    normalized_name: str
    definition: str | None
    method: str
    score: float
    chunk_ordinal: int | None = None

    @property
    def has_definition(self) -> bool:
        return bool(self.definition and self.definition.strip())


@dataclass(frozen=True, slots=True)
class ExtractionConfig:
    """Thresholds governing what counts as a concept."""

    #: A frequency term must appear at least this often to be a candidate.
    min_term_occurrences: int = 3
    #: Cap on candidates returned, so a long document does not produce noise.
    max_concepts: int = 40
    #: Shortest acceptable concept name, in characters.
    min_name_chars: int = 3
    #: Longest definition kept, in characters; longer ones are truncated.
    max_definition_chars: int = 400
    #: A definition must have at least this much substance. Without a floor,
    #: "Performance is important here." registers as a definition of
    #: "Performance", which is exactly the kind of noise that makes generated
    #: study material untrustworthy.
    min_definition_words: int = 5
    min_definition_chars: int = 25

    def __post_init__(self) -> None:
        if self.min_term_occurrences < 1:
            raise ValueError("min_term_occurrences must be at least 1")
        if self.max_concepts < 1:
            raise ValueError("max_concepts must be at least 1")


_PLURAL_RULES = (("ies", "y"), ("sses", "ss"), ("shes", "sh"), ("ches", "ch"), ("xes", "x"))


def normalize_concept_name(name: str) -> str:
    """The deduplication key for a concept.

    Lowercases, collapses whitespace, strips surrounding punctuation and applies
    a small set of pluralisation rules, so "AVL Trees", "avl tree" and
    "AVL  Tree" all converge on one concept rather than three.

    Deliberately *not* a stemmer: aggressive stemming collapses genuinely
    different technical terms ("recursion"/"recursive" are not the same concept),
    and a wrong merge is far more damaging than a missed one.
    """
    # Dashes are folded to ASCII by normalize_text, but concept names can also
    # arrive straight from a user typing into a form, so strip both forms.
    cleaned = _WHITESPACE.sub(" ", name).strip().strip(_NAME_TRIM_CHARS).lower()
    if not cleaned:
        return ""
    words = cleaned.split()
    words[-1] = _singularize(words[-1])
    return " ".join(words)


def _singularize(word: str) -> str:
    for suffix, replacement in _PLURAL_RULES:
        if word.endswith(suffix) and len(word) > len(suffix) + 1:
            return word[: -len(suffix)] + replacement
    if word.endswith("s") and not word.endswith(("ss", "us", "is")) and len(word) > 3:
        return word[:-1]
    return word


def extract_concepts(
    text: str,
    *,
    config: ExtractionConfig | None = None,
    headings: list[str] | None = None,
    chunk_ordinal: int | None = None,
) -> list[ConceptCandidate]:
    """Extract ranked concept candidates from normalised text.

    Deterministic: the same input always yields the same list in the same order.
    Returns an empty list rather than noise when there is nothing to find.
    """
    config = config or ExtractionConfig()
    if not text or not text.strip():
        return []

    found: dict[str, ConceptCandidate] = {}

    # Strongest evidence first, so a later, weaker match never overwrites a
    # definition that has already been established for the same concept.
    for candidate in (
        *_from_glossary_lines(text, config, chunk_ordinal),
        *_from_definition_sentences(text, config, chunk_ordinal),
        *_from_headings(headings or [], config, chunk_ordinal),
        *_from_frequency(text, config, chunk_ordinal),
    ):
        _merge(found, candidate)

    ranked = sorted(found.values(), key=lambda c: (-c.score, c.normalized_name))
    return ranked[: config.max_concepts]


def _merge(found: dict[str, ConceptCandidate], candidate: ConceptCandidate) -> None:
    """Keep the best evidence for each normalised concept.

    A concept already carrying a definition is never downgraded to a bare
    frequency hit, but a frequency hit can gain a definition it did not have.
    """
    existing = found.get(candidate.normalized_name)
    if existing is None:
        found[candidate.normalized_name] = candidate
        return
    if candidate.score > existing.score or (
        candidate.has_definition and not existing.has_definition
    ):
        found[candidate.normalized_name] = ConceptCandidate(
            name=existing.name if existing.score >= candidate.score else candidate.name,
            normalized_name=existing.normalized_name,
            definition=existing.definition or candidate.definition,
            method=candidate.method if candidate.score > existing.score else existing.method,
            score=max(existing.score, candidate.score),
            chunk_ordinal=existing.chunk_ordinal,
        )


def _acceptable_term(term: str, config: ExtractionConfig) -> bool:
    """Reject stopwords, fragments and things that are not really terms."""
    normalized = normalize_concept_name(term)
    if len(normalized) < config.min_name_chars:
        return False
    words = normalized.split()
    if not words or len(words) > 5:
        return False
    # Every word a stopword means this is a sentence fragment, not a term.
    if all(word in STOPWORDS for word in words):
        return False
    # A term must not *start* with a stopword: "The Binary Tree" is the concept
    # "Binary Tree" with an article glued on.
    if words[0] in STOPWORDS:
        return False
    return any(character.isalpha() for character in normalized)


def _is_substantive_definition(definition: str, config: ExtractionConfig) -> bool:
    """Whether a matched clause is a real definition or incidental prose.

    A pure length-and-word-count heuristic. It cannot tell a definition from a
    long unrelated statement -- that would need parsing this module explicitly
    does not do -- but it reliably removes the short adjectival matches
    ("X is important", "Y is useful") that dominate the false positives.
    """
    text = definition.strip()
    return (
        len(text) >= config.min_definition_chars
        and len(text.split()) >= config.min_definition_words
    )


def _clean_definition(definition: str, config: ExtractionConfig) -> str:
    text = _WHITESPACE.sub(" ", definition).strip().rstrip(",;:")
    if not text.endswith((".", "!", "?")):
        text += "."
    if len(text) > config.max_definition_chars:
        text = text[: config.max_definition_chars].rsplit(" ", 1)[0] + "..."
    return text[0].upper() + text[1:] if text else text


def _from_definition_sentences(
    text: str, config: ExtractionConfig, chunk_ordinal: int | None
) -> list[ConceptCandidate]:
    """ "An AVL tree is a self-balancing binary search tree." """
    candidates: list[ConceptCandidate] = []
    for sentence in split_sentences(text):
        match = _DEFINITION_SENTENCE.match(sentence.strip())
        if not match:
            continue
        term = match.group("term").strip()
        # "A binary search tree is ..." is the commonest definition frame in
        # technical writing, and its term is lowercase. An explicit leading
        # article is itself the signal that a definition follows, so a lowercase
        # term is accepted there. Without an article we require a capital,
        # which keeps out ordinary prose like "performance is important".
        # Pronoun subjects ("It is a tree") are caught by _acceptable_term,
        # which rejects any term whose first word is a stopword.
        if not match.group("article") and not term[:1].isupper():
            continue
        if not _acceptable_term(term, config):
            continue
        if not _is_substantive_definition(match.group("definition"), config):
            continue
        candidates.append(
            ConceptCandidate(
                name=term,
                normalized_name=normalize_concept_name(term),
                definition=_clean_definition(match.group("definition"), config),
                method=Method.DEFINITION,
                score=_METHOD_SCORE[Method.DEFINITION],
                chunk_ordinal=chunk_ordinal,
            )
        )
    return candidates


def _from_glossary_lines(
    text: str, config: ExtractionConfig, chunk_ordinal: int | None
) -> list[ConceptCandidate]:
    """ "Stability: days until recall decays to 90%." on its own line."""
    candidates: list[ConceptCandidate] = []
    for raw_line in text.split("\n"):
        line = raw_line.strip().lstrip("-*•").strip()
        if not line:
            continue
        match = _COLON_DEFINITION.match(line) or _DASH_DEFINITION.match(line)
        if not match:
            continue
        term = match.group("term").strip()
        if not _acceptable_term(term, config):
            continue
        if not _is_substantive_definition(match.group("definition"), config):
            continue
        candidates.append(
            ConceptCandidate(
                name=term,
                normalized_name=normalize_concept_name(term),
                definition=_clean_definition(match.group("definition"), config),
                method=Method.GLOSSARY,
                score=_METHOD_SCORE[Method.GLOSSARY],
                chunk_ordinal=chunk_ordinal,
            )
        )
    return candidates


def _from_headings(
    headings: list[str], config: ExtractionConfig, chunk_ordinal: int | None
) -> list[ConceptCandidate]:
    """An author who gave something its own heading thinks it matters."""
    candidates: list[ConceptCandidate] = []
    for heading in headings:
        term = heading.strip()
        if not _acceptable_term(term, config):
            continue
        candidates.append(
            ConceptCandidate(
                name=term,
                normalized_name=normalize_concept_name(term),
                definition=None,
                method=Method.HEADING,
                score=_METHOD_SCORE[Method.HEADING],
                chunk_ordinal=chunk_ordinal,
            )
        )
    return candidates


def _from_frequency(
    text: str, config: ExtractionConfig, chunk_ordinal: int | None
) -> list[ConceptCandidate]:
    """Terms the document keeps coming back to.

    The weakest evidence, and scored accordingly. A term appearing many times
    might be central -- or might be a word the author simply likes.
    """
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}

    for raw in _TERM_PHRASE.findall(text) + _ACRONYM.findall(text):
        term = raw.strip()
        if not _acceptable_term(term, config):
            continue
        key = normalize_concept_name(term)
        counts[key] += 1
        # Keep the longest surface form seen: "Binary Search Tree" reads better
        # than the "Binary Search" that a sentence-initial match might yield.
        if key not in display or len(term) > len(display[key]):
            display[key] = term

    candidates: list[ConceptCandidate] = []
    if not counts:
        return candidates

    highest = max(counts.values())
    for key, count in counts.items():
        if count < config.min_term_occurrences:
            continue
        # Scale within the frequency band only; a frequency hit can never
        # outrank a glossary or definition match.
        weight = _METHOD_SCORE[Method.FREQUENCY] * (0.5 + 0.5 * count / highest)
        candidates.append(
            ConceptCandidate(
                name=display[key],
                normalized_name=key,
                definition=None,
                method=Method.FREQUENCY,
                score=round(weight, 4),
                chunk_ordinal=chunk_ordinal,
            )
        )
    return candidates
