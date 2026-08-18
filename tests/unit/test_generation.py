"""Deterministic generation: quality bar, provenance, and knowing when to stop."""

from __future__ import annotations

import pytest

from studyforge.domain.generation.flashcards import (
    CLOZE_BLANK,
    CardStrategy,
    generate_cards,
)
from studyforge.domain.generation.quizzes import (
    CHOICE_COUNT,
    DISTRACTORS_REQUIRED,
    QuestionKind,
    generate_questions,
)
from studyforge.domain.generation.source import ConceptSource

AVL = ConceptSource(
    concept_id=1,
    name="AVL tree",
    normalized_name="avl tree",
    definition=(
        "A self-balancing binary search tree that keeps every balance factor "
        "within negative one, zero and one."
    ),
    source_document_id=10,
    source_chunk_id=100,
    score=1.0,
)
HEAP = ConceptSource(
    concept_id=2,
    name="Heap",
    normalized_name="heap",
    definition="A complete binary tree satisfying the heap ordering property at every node.",
    score=0.9,
)


def filler(count: int, *, start: int = 50) -> list[ConceptSource]:
    return [
        ConceptSource(
            concept_id=start + i,
            name=f"Concept {start + i}",
            normalized_name=f"concept {start + i}",
            definition=f"A distinctive idea number {start + i} worth remembering for the exam.",
            score=0.5,
        )
        for i in range(count)
    ]


class TestFlashcardQualityBar:
    """Fewer, defensible cards over more, weak ones."""

    def test_a_concept_without_a_definition_produces_nothing(self) -> None:
        bare = ConceptSource(9, "Mystery", "mystery", None)
        assert generate_cards([bare]) == []

    @pytest.mark.parametrize("definition", ["", "   ", "Too brief.", "A B C."])
    def test_an_insubstantial_definition_produces_nothing(self, definition: str) -> None:
        weak = ConceptSource(9, "Thing", "thing", definition)
        assert generate_cards([weak]) == []

    def test_an_essay_length_definition_is_refused(self) -> None:
        essay = ConceptSource(9, "Thing", "thing", "word " * 500)
        assert generate_cards([essay]) == []

    def test_a_one_character_name_is_refused(self) -> None:
        tiny = ConceptSource(9, "X", "x", AVL.definition)
        assert generate_cards([tiny]) == []

    def test_no_card_has_an_empty_side(self) -> None:
        for card in generate_cards([AVL, HEAP], strategies=tuple(CardStrategy)):
            assert card.front.strip()
            assert card.back.strip()


class TestFlashcardStrategies:
    def test_term_to_definition_asks_for_the_meaning(self) -> None:
        [card] = generate_cards([AVL], strategies=(CardStrategy.TERM_TO_DEFINITION,))
        assert card.front == "Define: AVL tree"
        assert card.back == AVL.definition

    def test_definition_to_term_hides_the_answer_in_the_prompt(self) -> None:
        """Otherwise the card gives itself away."""
        [card] = generate_cards([HEAP], strategies=(CardStrategy.DEFINITION_TO_TERM,))
        assert "heap" not in card.front.lower()
        assert CLOZE_BLANK in card.front
        assert card.back == "Heap"

    def test_cloze_is_declined_when_the_term_is_absent_from_its_definition(self) -> None:
        """A blank in a random place is not a cloze."""
        assert generate_cards([AVL], strategies=(CardStrategy.CLOZE,)) == []

    def test_cloze_is_produced_when_the_term_does_appear(self) -> None:
        [card] = generate_cards([HEAP], strategies=(CardStrategy.CLOZE,))
        assert CLOZE_BLANK in card.front
        assert card.back == "Heap"

    @pytest.mark.parametrize(
        "name",
        ["AVL tree", "Heap", "BST", "Big-O notation", "amortised analysis", "Quicksort"],
    )
    def test_the_prompt_is_grammatical_for_every_kind_of_term(self, name: str) -> None:
        """Mass nouns, proper nouns, acronyms and ordinary count nouns all read
        correctly, which an article-inserting rule cannot manage without
        part-of-speech tagging."""
        concept = ConceptSource(1, name, name.lower(), AVL.definition)
        [card] = generate_cards([concept], strategies=(CardStrategy.TERM_TO_DEFINITION,))
        assert card.front == f"Define: {name}"
        assert " a Big-O" not in card.front
        assert " an Quicksort" not in card.front


class TestFlashcardProvenance:
    def test_every_card_points_back_at_its_source(self) -> None:
        for card in generate_cards([AVL], strategies=tuple(CardStrategy)):
            assert card.concept_id == 1
            assert card.source_document_id == 10
            assert card.source_chunk_id == 100

    def test_the_dedupe_key_is_concept_plus_shape(self) -> None:
        cards = generate_cards([AVL], strategies=(CardStrategy.TERM_TO_DEFINITION,))
        assert cards[0].dedupe_key == (1, "term_to_definition")


class TestFlashcardDeterminism:
    def test_repeated_generation_is_identical(self) -> None:
        runs = [generate_cards([HEAP, AVL], strategies=tuple(CardStrategy)) for _ in range(5)]
        assert all(run == runs[0] for run in runs)

    def test_input_order_does_not_change_the_output(self) -> None:
        assert generate_cards([AVL, HEAP]) == generate_cards([HEAP, AVL])

    def test_the_cap_is_respected(self) -> None:
        assert len(generate_cards(filler(50), max_cards=7)) == 7


class TestQuizDistractors:
    """The failure mode that makes generated MCQs worthless is fake distractors."""

    def test_no_multiple_choice_without_enough_sibling_concepts(self) -> None:
        questions = generate_questions([AVL, HEAP])
        assert questions
        assert all(q.kind is QuestionKind.SHORT_ANSWER for q in questions)

    def test_multiple_choice_appears_once_the_course_can_supply_distractors(self) -> None:
        questions = generate_questions([AVL, HEAP, *filler(DISTRACTORS_REQUIRED)])
        assert any(q.is_multiple_choice for q in questions)

    def test_every_choice_is_a_real_definition_from_the_course(self) -> None:
        pool = [AVL, HEAP, *filler(4)]
        definitions = {c.definition for c in pool}
        for question in generate_questions(pool):
            for choice in question.choices:
                assert choice in definitions, "distractors must come from the course"

    def test_choices_are_never_duplicated(self) -> None:
        for question in generate_questions([AVL, HEAP, *filler(5)]):
            assert len(set(question.choices)) == len(question.choices)

    def test_a_shared_definition_is_not_used_as_its_own_distractor(self) -> None:
        """Two concepts can extract to the same definition; using both would
        produce a question with two correct answers."""
        twin = ConceptSource(99, "Twin", "twin", AVL.definition, score=0.95)
        for question in generate_questions([AVL, twin, *filler(5)]):
            assert len(set(question.choices)) == len(question.choices)

    def test_multiple_choice_has_exactly_four_options(self) -> None:
        for question in generate_questions([AVL, HEAP, *filler(6)]):
            if question.is_multiple_choice:
                assert len(question.choices) == CHOICE_COUNT

    def test_the_correct_index_points_at_the_expected_answer(self) -> None:
        for question in generate_questions([AVL, HEAP, *filler(6)]):
            if question.is_multiple_choice:
                assert question.correct_choice_index is not None
                assert question.choices[question.correct_choice_index] == question.expected_answer

    def test_the_answer_is_not_always_in_the_same_position(self) -> None:
        positions = {
            q.correct_choice_index
            for q in generate_questions([AVL, HEAP, *filler(10)], max_questions=10)
            if q.is_multiple_choice
        }
        assert len(positions) > 1, "a fixed answer position is trivially gameable"


class TestQuizGeneration:
    def test_no_concepts_yields_no_questions(self) -> None:
        assert generate_questions([]) == []

    def test_concepts_without_definitions_yield_no_questions(self) -> None:
        bare = [ConceptSource(i, f"C{i}", f"c{i}", None) for i in range(10)]
        assert generate_questions(bare) == []

    def test_fewer_questions_are_produced_rather_than_padding(self) -> None:
        assert len(generate_questions([AVL, HEAP], max_questions=10)) == 2

    def test_the_maximum_is_respected(self) -> None:
        assert len(generate_questions(filler(50), max_questions=6)) == 6

    def test_every_question_is_attributed_to_a_concept(self) -> None:
        """Wrong answers must be able to feed the weak-concept analysis."""
        for question in generate_questions([AVL, HEAP, *filler(5)]):
            assert question.concept_id is not None

    def test_every_question_carries_an_explanation(self) -> None:
        for question in generate_questions([AVL, HEAP, *filler(5)]):
            assert question.explanation

    def test_multiple_choice_can_be_disabled(self) -> None:
        questions = generate_questions([AVL, HEAP, *filler(5)], allow_multiple_choice=False)
        assert all(q.kind is QuestionKind.SHORT_ANSWER for q in questions)

    def test_disabling_both_kinds_yields_nothing(self) -> None:
        assert (
            generate_questions(
                [AVL, HEAP, *filler(5)],
                allow_multiple_choice=False,
                allow_short_answer=False,
            )
            == []
        )

    def test_generation_is_deterministic(self) -> None:
        pool = [AVL, HEAP, *filler(6)]
        runs = [generate_questions(pool) for _ in range(5)]
        assert all(run == runs[0] for run in runs)
