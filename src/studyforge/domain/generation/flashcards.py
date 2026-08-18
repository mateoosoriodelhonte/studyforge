"""Turn concepts into flashcards, deterministically.

Three strategies, in descending order of how well they test understanding:

``TERM_TO_DEFINITION``
    "What is an AVL tree?" -> the definition. Recall of meaning.

``DEFINITION_TO_TERM``
    "Which term is described here: ...?" -> the name. Recognition from
    description, which is the direction exams usually test.

``CLOZE``
    The definition with the term blanked out. Only produced when the term
    actually occurs inside its own definition, which is when a cloze reads
    naturally; otherwise it degenerates into a sentence with a random hole in it.

Every candidate carries provenance back to the concept and chunk it came from.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass

from studyforge.domain.generation.source import ConceptSource

#: A definition shorter than this does not carry enough information to answer
#: a question with, and longer than this is a passage, not a flashcard answer.
MIN_DEFINITION_CHARS = 25
MAX_DEFINITION_CHARS = 400

#: Below this the "definition" is usually a restatement of the term itself.
MIN_DEFINITION_WORDS = 5


class CardStrategy(enum.StrEnum):
    TERM_TO_DEFINITION = "term_to_definition"
    DEFINITION_TO_TERM = "definition_to_term"
    CLOZE = "cloze"


#: The blank shown in a cloze card. Fixed width so it cannot hint at the
#: length of the answer.
CLOZE_BLANK = "______"


@dataclass(frozen=True, slots=True)
class CardCandidate:
    """A proposed flashcard, not yet accepted by the learner."""

    front: str
    back: str
    strategy: CardStrategy
    concept_id: int
    source_document_id: int | None = None
    source_chunk_id: int | None = None

    @property
    def dedupe_key(self) -> tuple[int, str]:
        """Identity for "have we already made this card?" checks."""
        return (self.concept_id, self.strategy.value)


def generate_cards(
    concepts: list[ConceptSource],
    *,
    strategies: tuple[CardStrategy, ...] = (
        CardStrategy.TERM_TO_DEFINITION,
        CardStrategy.DEFINITION_TO_TERM,
    ),
    max_cards: int | None = None,
) -> list[CardCandidate]:
    """Propose flashcards for ``concepts``.

    Concepts without a usable definition produce nothing. That is the whole
    quality bar: with no definition there is no defensible answer side, and a
    card whose back is a guess is worse than no card.
    """
    candidates: list[CardCandidate] = []

    # Sort so output order never depends on the caller's iteration order.
    for concept in sorted(concepts, key=lambda c: (-c.score, c.normalized_name)):
        if not _usable_definition(concept):
            continue
        for strategy in strategies:
            card = _build(concept, strategy)
            if card is not None:
                candidates.append(card)

    return candidates[:max_cards] if max_cards is not None else candidates


def _usable_definition(concept: ConceptSource) -> bool:
    if not concept.has_definition:
        return False
    definition = (concept.definition or "").strip()
    return (
        MIN_DEFINITION_CHARS <= len(definition) <= MAX_DEFINITION_CHARS
        and len(definition.split()) >= MIN_DEFINITION_WORDS
        and len(concept.name.strip()) >= 2
    )


def _build(concept: ConceptSource, strategy: CardStrategy) -> CardCandidate | None:
    definition = (concept.definition or "").strip()
    name = concept.name.strip()

    match strategy:
        case CardStrategy.TERM_TO_DEFINITION:
            front, back = f"What is {_article(name)}{name}?", definition
        case CardStrategy.DEFINITION_TO_TERM:
            front = f"Which term is described here?\n\n{_blank_out(definition, name)[0]}"
            back = name
        case CardStrategy.CLOZE:
            blanked, replacements = _blank_out(definition, name)
            if replacements == 0:
                # The term does not appear in its own definition, so there is
                # nothing to blank out and a cloze would be a sentence with a
                # hole in a random place.
                return None
            front, back = blanked, name

    # No wildcard case: the match is exhaustive over CardStrategy, and mypy
    # verifies that. Adding a member without handling it here is a type error.
    if not front or not back:
        return None

    return CardCandidate(
        front=front,
        back=back,
        strategy=strategy,
        concept_id=concept.concept_id,
        source_document_id=concept.source_document_id,
        source_chunk_id=concept.source_chunk_id,
    )


def _blank_out(definition: str, term: str) -> tuple[str, int]:
    """Replace occurrences of ``term`` with a blank; report how many.

    Without this, a "which term is described here?" card routinely gives the
    answer away in its own prompt. The count is what lets the cloze strategy
    decline when the term never appears in its own definition.
    """
    pattern = re.compile(rf"\b{re.escape(term)}(?:e?s)?\b", re.IGNORECASE)
    return pattern.subn(CLOZE_BLANK, definition)


def _article(name: str) -> str:
    """ "an AVL tree" vs "a binary tree".

    Uses the first letter's sound where the common cases allow. Proper nouns and
    acronyms are left bare, since "What is a HTTP?" reads badly.
    """
    first = name.strip()[:1]
    if not first.isalpha():
        return ""
    if name.strip().isupper():
        return ""
    return "an " if first.lower() in "aeiou" else "a "
