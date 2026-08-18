"""Turn concepts into quiz questions, deterministically.

Multiple choice is where deterministic generation usually goes wrong. The
failure mode is obvious once you see it: three plausible-looking distractors
invented from nothing, or worse, filler like "None of the above". A learner
answers such a question correctly by elimination and learns nothing.

StudyForge only builds a multiple-choice question when the course itself
supplies real distractors -- the definitions of *sibling concepts from the same
course*. Same domain, same register, genuinely confusable. If a course does not
yet have enough sibling concepts with definitions, **no multiple-choice question
is generated for that concept.** Fewer questions, defensible ones.

Short-answer questions have no such constraint, so a small course still gets a
usable quiz.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from studyforge.domain.generation.source import ConceptSource

#: A multiple-choice question needs one correct answer plus this many
#: distractors. Fewer than three distractors makes guessing too cheap.
DISTRACTORS_REQUIRED = 3
CHOICE_COUNT = DISTRACTORS_REQUIRED + 1

MIN_DEFINITION_CHARS = 25
MIN_DEFINITION_WORDS = 5


class QuestionKind(enum.StrEnum):
    MULTIPLE_CHOICE = "multiple_choice"
    SHORT_ANSWER = "short_answer"


@dataclass(frozen=True, slots=True)
class QuestionCandidate:
    """A proposed question, with everything needed to grade and explain it."""

    kind: QuestionKind
    prompt: str
    expected_answer: str
    concept_id: int
    explanation: str | None = None
    choices: tuple[str, ...] = ()
    correct_choice_index: int | None = None
    source_document_id: int | None = None
    source_chunk_id: int | None = None

    @property
    def is_multiple_choice(self) -> bool:
        return self.kind is QuestionKind.MULTIPLE_CHOICE


def generate_questions(
    concepts: list[ConceptSource],
    *,
    max_questions: int = 10,
    allow_multiple_choice: bool = True,
    allow_short_answer: bool = True,
) -> list[QuestionCandidate]:
    """Propose quiz questions for ``concepts``.

    Deterministic: the same concepts always yield the same questions, with the
    same choices in the same order.
    """
    usable = sorted(
        (c for c in concepts if _usable(c)),
        key=lambda c: (-c.score, c.normalized_name),
    )
    if not usable:
        return []

    questions: list[QuestionCandidate] = []
    for concept in usable:
        if len(questions) >= max_questions:
            break

        if allow_multiple_choice:
            distractors = _pick_distractors(concept, usable)
            if distractors is not None:
                questions.append(_multiple_choice(concept, distractors))
                continue

        if allow_short_answer:
            questions.append(_short_answer(concept))

    return questions[:max_questions]


def _usable(concept: ConceptSource) -> bool:
    if not concept.has_definition:
        return False
    definition = (concept.definition or "").strip()
    return (
        len(definition) >= MIN_DEFINITION_CHARS
        and len(definition.split()) >= MIN_DEFINITION_WORDS
        and len(concept.name.strip()) >= 2
    )


def _pick_distractors(concept: ConceptSource, pool: list[ConceptSource]) -> tuple[str, ...] | None:
    """Choose sibling definitions to sit alongside the correct one.

    Returns ``None`` -- meaning "do not generate a multiple-choice question
    here" -- when the course cannot supply enough genuine alternatives.

    Selection is by descending concept score, so the distractors are the most
    prominent other ideas in the course: the ones a learner is most likely to
    confuse with this concept, which is exactly what makes a distractor useful.
    """
    seen: set[str] = {_normalise_choice(concept.definition or "")}
    chosen: list[str] = []

    for sibling in pool:
        if sibling.concept_id == concept.concept_id:
            continue
        definition = (sibling.definition or "").strip()
        key = _normalise_choice(definition)
        # Two concepts can share a near-identical definition after extraction;
        # using both would produce a question with two correct answers.
        if key in seen:
            continue
        seen.add(key)
        chosen.append(definition)
        if len(chosen) == DISTRACTORS_REQUIRED:
            return tuple(chosen)

    return None


def _multiple_choice(concept: ConceptSource, distractors: tuple[str, ...]) -> QuestionCandidate:
    correct = (concept.definition or "").strip()

    # Ordering must be deterministic but must not always put the answer first.
    # Sorting the choices alphabetically achieves both: reproducible, and the
    # correct answer's position varies with its text rather than its role.
    choices = tuple(sorted([correct, *distractors]))
    return QuestionCandidate(
        kind=QuestionKind.MULTIPLE_CHOICE,
        prompt=f"Which of these best describes {concept.name.strip()}?",
        expected_answer=correct,
        concept_id=concept.concept_id,
        explanation=f"{concept.name.strip()}: {correct}",
        choices=choices,
        correct_choice_index=choices.index(correct),
        source_document_id=concept.source_document_id,
        source_chunk_id=concept.source_chunk_id,
    )


def _short_answer(concept: ConceptSource) -> QuestionCandidate:
    definition = (concept.definition or "").strip()
    return QuestionCandidate(
        kind=QuestionKind.SHORT_ANSWER,
        prompt=f"In your own words, what is {concept.name.strip()}?",
        expected_answer=definition,
        concept_id=concept.concept_id,
        explanation=f"{concept.name.strip()}: {definition}",
        source_document_id=concept.source_document_id,
        source_chunk_id=concept.source_chunk_id,
    )


def _normalise_choice(text: str) -> str:
    return " ".join(text.lower().split())
