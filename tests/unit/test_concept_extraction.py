"""Deterministic concept extraction: each evidence source, ranking and noise rejection."""

from __future__ import annotations

import pytest

from studyforge.domain.concepts.extract import (
    ConceptCandidate,
    ExtractionConfig,
    Method,
    extract_concepts,
    normalize_concept_name,
)
from studyforge.domain.text.normalize import normalize_text

LECTURE = normalize_text(
    """
Binary Search Trees

A binary search tree is a rooted binary tree in which every node stores a key
greater than all keys in its left subtree.

Balance factor: the height of a node's right subtree minus the height of its
left subtree.

Rotation - a local restructuring operation that restores the tree invariant
after an insertion or a deletion.

An AVL tree is a self-balancing binary search tree that keeps every balance
factor within the set negative one, zero and one.

The AVL tree guarantees logarithmic height. Every AVL tree operation therefore
runs in logarithmic time, and the AVL tree is named after its inventors.
"""
)


def names(candidates: list[ConceptCandidate]) -> set[str]:
    return {c.normalized_name for c in candidates}


def by_name(candidates: list[ConceptCandidate], name: str) -> ConceptCandidate:
    return next(c for c in candidates if c.normalized_name == name)


class TestNormalizeConceptName:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("AVL Tree", "avl tree"),
            ("AVL Trees", "avl tree"),
            ("avl  tree", "avl tree"),
            ("  AVL Tree  ", "avl tree"),
            ("AVL Tree.", "avl tree"),
            ("(AVL Tree)", "avl tree"),
            ("Binary Search Trees", "binary search tree"),
            ("Properties", "property"),
            ("Classes", "class"),
            ("Matches", "match"),
            ("Boxes", "box"),
            ("Analysis", "analysis"),
            ("Bus", "bus"),
        ],
    )
    def test_converges_on_one_key(self, raw: str, expected: str) -> None:
        assert normalize_concept_name(raw) == expected

    def test_does_not_stem_aggressively(self) -> None:
        """Recursion and recursive are different concepts; merging them is worse
        than keeping both."""
        assert normalize_concept_name("Recursion") != normalize_concept_name("Recursive")

    @pytest.mark.parametrize("raw", ["", "   ", "..."])
    def test_degenerate_input_yields_an_empty_key(self, raw: str) -> None:
        assert normalize_concept_name(raw) == ""


class TestDefinitionSentences:
    def test_finds_a_definition_with_a_leading_article(self) -> None:
        """ "A binary search tree is ..." is the commonest definition frame."""
        found = extract_concepts(LECTURE)
        concept = by_name(found, "binary search tree")
        assert concept.method == Method.DEFINITION
        assert "rooted binary tree" in (concept.definition or "")

    def test_finds_a_definition_without_an_article(self) -> None:
        text = "Stability is the number of days until predicted recall decays to ninety percent."
        [concept] = [c for c in extract_concepts(text) if c.method == Method.DEFINITION]
        assert concept.normalized_name == "stability"

    @pytest.mark.parametrize(
        "verb", ["means", "refers to", "denotes", "describes", "is defined as", "is known as"]
    )
    def test_recognises_each_definition_verb(self, verb: str) -> None:
        text = f"Amortisation {verb} the averaging of cost across a long sequence of operations."
        assert any(c.method == Method.DEFINITION for c in extract_concepts(text))

    def test_rejects_a_pronoun_subject(self) -> None:
        found = extract_concepts("It is a rooted binary tree with sorted keys inside it.")
        assert "it" not in names(found)

    def test_rejects_an_insubstantial_definition(self) -> None:
        """ "Performance is important." is prose, not a definition."""
        found = extract_concepts("Performance is important. Speed is good. Memory is limited.")
        assert not [c for c in found if c.method == Method.DEFINITION]

    def test_definitions_are_punctuated_and_capitalised(self) -> None:
        concept = by_name(extract_concepts(LECTURE), "binary search tree")
        assert concept.definition is not None
        assert concept.definition[0].isupper()
        assert concept.definition.endswith(".")

    def test_a_very_long_definition_is_truncated(self) -> None:
        long_tail = " ".join(["clause"] * 300)
        text = f"Widget is a component that {long_tail}."
        config = ExtractionConfig(max_definition_chars=120)
        concept = by_name(extract_concepts(text, config=config), "widget")
        assert concept.definition is not None
        assert len(concept.definition) <= 124
        assert concept.definition.endswith("...")


class TestGlossaryLines:
    def test_finds_a_colon_definition(self) -> None:
        concept = by_name(extract_concepts(LECTURE), "balance factor")
        assert concept.method == Method.GLOSSARY
        assert "height" in (concept.definition or "")

    def test_finds_a_dash_definition(self) -> None:
        concept = by_name(extract_concepts(LECTURE), "rotation")
        assert concept.method == Method.GLOSSARY

    def test_finds_a_bulleted_glossary_entry(self) -> None:
        text = "- Heap: a complete binary tree satisfying the heap ordering property everywhere."
        assert "heap" in names(extract_concepts(text))

    def test_a_colon_with_a_trivial_tail_is_not_a_definition(self) -> None:
        assert not extract_concepts("Note: see above.")


class TestHeadings:
    def test_a_heading_becomes_a_candidate(self) -> None:
        found = extract_concepts("Some body text about things.", headings=["Red-Black Trees"])
        assert "red-black tree" in names(found)

    def test_a_heading_scores_below_a_definition(self) -> None:
        found = extract_concepts(LECTURE, headings=["Binary Search Trees"])
        concept = by_name(found, "binary search tree")
        assert concept.method == Method.DEFINITION, "definition evidence must win"

    def test_a_stopword_heading_is_rejected(self) -> None:
        assert not extract_concepts("body", headings=["The", "And", "Notes"])


class TestFrequency:
    def test_a_repeated_term_becomes_a_candidate(self) -> None:
        text = "Quicksort is fast. Quicksort uses partitioning. Quicksort is in-place."
        found = extract_concepts(text, config=ExtractionConfig(min_term_occurrences=3))
        assert "quicksort" in names(found)

    def test_the_occurrence_threshold_is_respected(self) -> None:
        text = "Mergesort appears. Mergesort appears again."
        assert "mergesort" not in names(
            extract_concepts(text, config=ExtractionConfig(min_term_occurrences=3))
        )

    def test_frequency_never_outranks_a_definition(self) -> None:
        found = extract_concepts(LECTURE)
        definitions = [c.score for c in found if c.method == Method.DEFINITION]
        frequencies = [c.score for c in found if c.method == Method.FREQUENCY]
        assert not frequencies or max(frequencies) < min(definitions)

    def test_the_longest_surface_form_is_kept(self) -> None:
        text = (
            "Binary Search Tree lookup is fast. Binary Search Tree insertion is fast. "
            "Binary Search Tree deletion is fast."
        )
        found = extract_concepts(text, config=ExtractionConfig(min_term_occurrences=3))
        assert by_name(found, "binary search tree").name == "Binary Search Tree"


class TestNoiseRejection:
    def test_stopwords_never_surface(self) -> None:
        found = extract_concepts(LECTURE, headings=["The", "And"])
        assert not (names(found) & {"the", "and", "this", "that", "it", "is"})

    def test_no_concept_starts_with_an_article(self) -> None:
        for concept in extract_concepts(LECTURE):
            assert concept.normalized_name.split()[0] not in {"a", "an", "the"}

    @pytest.mark.parametrize("text", ["", "   ", "..!!??", "a", "the the the the"])
    def test_content_free_input_yields_nothing(self, text: str) -> None:
        assert extract_concepts(text) == []

    def test_names_are_bounded_in_length(self) -> None:
        for concept in extract_concepts(LECTURE):
            assert len(concept.normalized_name) >= 3
            assert len(concept.normalized_name.split()) <= 5


class TestRankingAndMerging:
    def test_results_are_ordered_by_descending_score(self) -> None:
        scores = [c.score for c in extract_concepts(LECTURE, headings=["Binary Search Trees"])]
        assert scores == sorted(scores, reverse=True)

    def test_each_concept_appears_exactly_once(self) -> None:
        found = extract_concepts(LECTURE, headings=["Binary Search Trees", "AVL Trees"])
        keys = [c.normalized_name for c in found]
        assert len(keys) == len(set(keys))

    def test_a_heading_hit_gains_the_definition_found_in_the_body(self) -> None:
        found = extract_concepts(LECTURE, headings=["AVL Trees"])
        assert by_name(found, "avl tree").has_definition

    def test_the_result_count_is_capped(self) -> None:
        text = "\n".join(
            f"Concept{i} is a distinctive idea worth remembering for the examination."
            for i in range(60)
        )
        assert len(extract_concepts(text, config=ExtractionConfig(max_concepts=10))) == 10

    def test_the_chunk_ordinal_is_carried_through_for_provenance(self) -> None:
        found = extract_concepts(LECTURE, chunk_ordinal=7)
        assert all(c.chunk_ordinal == 7 for c in found)


class TestDeterminism:
    def test_repeated_extraction_is_identical(self) -> None:
        runs = [extract_concepts(LECTURE, headings=["AVL Trees"]) for _ in range(5)]
        assert all(run == runs[0] for run in runs)

    def test_candidates_are_immutable(self) -> None:
        candidate = extract_concepts(LECTURE)[0]
        with pytest.raises((AttributeError, TypeError)):
            candidate.score = 0.0  # type: ignore[misc]


class TestConfigValidation:
    @pytest.mark.parametrize("kwargs", [{"min_term_occurrences": 0}, {"max_concepts": 0}])
    def test_rejects_nonsense_thresholds(self, kwargs: dict[str, int]) -> None:
        with pytest.raises(ValueError):
            ExtractionConfig(**kwargs)
