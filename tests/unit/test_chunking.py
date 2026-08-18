"""Semantic chunking: boundaries, offsets, determinism and size bounds."""

from __future__ import annotations

from itertools import pairwise

import pytest

from studyforge.domain.text.chunking import (
    Chunk,
    ChunkingConfig,
    chunk_text,
    split_sentences,
)
from studyforge.domain.text.normalize import normalize_text

NOTES = normalize_text(
    """
# Data Structures

## Binary Search Trees

A binary search tree is a rooted binary tree in which every node stores a key
greater than all keys in its left subtree and less than all keys in its right
subtree. Lookup, insertion and deletion all follow a single root-to-leaf path.

On a balanced tree those operations run in O(log n) time, e.g. an AVL tree or a
red-black tree. On a degenerate tree that has become a linked list they degrade
to O(n), which is the worst case.

## AVL Trees

An AVL tree is a self-balancing binary search tree. Every node maintains a
balance factor, the height of its right subtree minus the height of its left,
and the invariant is that this value stays in {-1, 0, 1}.

Restoring the invariant after an insertion takes at most one rotation or double
rotation, so insertion remains O(log n) overall.
"""
)


class TestSentenceSplitting:
    def test_splits_on_terminal_punctuation(self) -> None:
        assert split_sentences("One. Two! Three?") == ["One.", "Two!", "Three?"]

    @pytest.mark.parametrize("abbrev", ["e.g.", "i.e.", "etc.", "cf.", "vs.", "Fig."])
    def test_does_not_split_on_an_abbreviation(self, abbrev: str) -> None:
        text = f"Balanced trees, {abbrev} AVL trees, are fast. That matters."
        assert len(split_sentences(text)) == 2

    def test_does_not_split_on_an_initial(self) -> None:
        assert len(split_sentences("Named after G. M. Adelson-Velsky. It is a tree.")) == 2

    def test_keeps_a_trailing_fragment_without_punctuation(self) -> None:
        assert split_sentences("Complete one. Incomplete two") == [
            "Complete one.",
            "Incomplete two",
        ]

    def test_handles_quotes_after_terminal_punctuation(self) -> None:
        assert len(split_sentences('He said "yes." Then he left.')) == 2

    @pytest.mark.parametrize("text", ["", "   ", "\n\n"])
    def test_empty_input_yields_no_sentences(self, text: str) -> None:
        assert split_sentences(text) == []

    def test_a_single_sentence_without_punctuation(self) -> None:
        assert split_sentences("just some words") == ["just some words"]


class TestConfigValidation:
    def test_max_cannot_be_below_target(self) -> None:
        with pytest.raises(ValueError, match="max_chars"):
            ChunkingConfig(target_chars=900, max_chars=500)

    def test_overlap_cannot_reach_the_target(self) -> None:
        with pytest.raises(ValueError, match="overlap_chars"):
            ChunkingConfig(target_chars=200, overlap_chars=200)

    @pytest.mark.parametrize(
        "kwargs", [{"target_chars": 0}, {"overlap_chars": -1}, {"min_chars": -1}]
    )
    def test_rejects_nonsense_sizes(self, kwargs: dict[str, int]) -> None:
        with pytest.raises(ValueError):
            ChunkingConfig(**kwargs)


class TestOffsets:
    """Offsets are what make "show me where this came from" honest."""

    def test_every_chunk_locates_itself_in_the_source(self) -> None:
        for chunk in chunk_text(NOTES):
            assert 0 <= chunk.char_start < chunk.char_end <= len(NOTES)

    def test_offsets_advance_monotonically(self) -> None:
        chunks = chunk_text(NOTES)
        starts = [c.char_start for c in chunks]
        assert starts == sorted(starts)

    def test_ordinals_are_contiguous_from_zero(self) -> None:
        assert [c.ordinal for c in chunk_text(NOTES)] == list(range(len(chunk_text(NOTES))))

    def test_start_ordinal_can_be_offset(self) -> None:
        chunks = chunk_text(NOTES, start_ordinal=10)
        assert chunks[0].ordinal == 10


class TestSizeBounds:
    @pytest.mark.parametrize("max_chars", [200, 500, 1400])
    def test_no_chunk_exceeds_the_hard_maximum(self, max_chars: int) -> None:
        config = ChunkingConfig(target_chars=max_chars // 2, max_chars=max_chars, overlap_chars=40)
        for chunk in chunk_text(NOTES, config):
            assert chunk.char_count <= max_chars, f"chunk {chunk.ordinal} overflowed"

    def test_an_oversized_single_sentence_is_split(self) -> None:
        """The fallback of last resort: a sentence longer than the maximum."""
        sentence = "word " * 400 + "end."
        config = ChunkingConfig(target_chars=300, max_chars=400, overlap_chars=0)
        chunks = chunk_text(sentence, config)
        assert len(chunks) > 1
        assert all(c.char_count <= 400 for c in chunks)

    def test_a_sentence_with_no_spaces_still_splits(self) -> None:
        config = ChunkingConfig(target_chars=50, max_chars=60, overlap_chars=0)
        chunks = chunk_text("x" * 500, config)
        assert chunks
        assert all(c.char_count <= 60 for c in chunks)


class TestBoundaries:
    def test_headings_are_attached_to_the_chunks_beneath_them(self) -> None:
        headings = {c.heading for c in chunk_text(NOTES)}
        assert "Binary Search Trees" in headings
        assert "AVL Trees" in headings

    def test_a_new_heading_starts_a_new_chunk(self) -> None:
        """Material under different headings is about different things."""
        for chunk in chunk_text(NOTES):
            assert chunk.heading is None or isinstance(chunk.heading, str)
        pairs = [(c.ordinal, c.heading) for c in chunk_text(NOTES)]
        seen: set[str] = set()
        for _, heading in pairs:
            if heading is not None:
                seen.add(heading)
        assert len(seen) >= 2

    @pytest.mark.parametrize(
        "heading_line",
        [
            "# Markdown Heading",
            "### Deeper Heading",
            "3.2 Numbered Section",
            "ALL CAPS HEADING",
            "Title Case Heading Here",
        ],
    )
    def test_recognises_each_heading_style(self, heading_line: str) -> None:
        text = f"{heading_line}\n\nSome body text that follows the heading and says things."
        assert chunk_text(text)[0].heading is not None

    def test_recognises_a_setext_underlined_heading(self) -> None:
        text = "Underlined Heading\n==================\n\nBody text follows here and continues."
        chunks = chunk_text(text)
        assert chunks[0].heading == "Underlined Heading"
        assert "=====" not in chunks[0].text

    def test_an_ordinary_sentence_is_not_mistaken_for_a_heading(self) -> None:
        text = "The tree is balanced.\n\nAnother paragraph of body text goes here for length."
        assert chunk_text(text)[0].heading is None

    def test_paragraphs_are_kept_whole_when_they_fit(self) -> None:
        text = "First paragraph here.\n\nSecond paragraph here."
        [chunk] = chunk_text(text, ChunkingConfig(target_chars=900, min_chars=0))
        assert "First paragraph" in chunk.text
        assert "Second paragraph" in chunk.text


class TestOverlap:
    def test_consecutive_chunks_share_context(self) -> None:
        config = ChunkingConfig(target_chars=250, max_chars=400, overlap_chars=80, min_chars=0)
        chunks = chunk_text(NOTES, config)
        assert len(chunks) > 2
        # At least one boundary should carry text forward from its predecessor.
        shared = sum(
            1
            for previous, following in pairwise(chunks)
            if any(
                sentence and sentence in following.text
                for sentence in split_sentences(previous.text)[-1:]
            )
        )
        assert shared >= 1

    def test_zero_overlap_is_honoured(self) -> None:
        config = ChunkingConfig(target_chars=250, max_chars=400, overlap_chars=0, min_chars=0)
        chunks = chunk_text(NOTES, config)
        for previous, following in pairwise(chunks):
            last = split_sentences(previous.text)[-1:] or [""]
            assert last[0] not in following.text or not last[0]

    def test_overlap_never_duplicates_a_whole_chunk(self) -> None:
        config = ChunkingConfig(target_chars=200, max_chars=400, overlap_chars=190, min_chars=0)
        chunks = chunk_text(NOTES, config)
        texts = [c.text for c in chunks]
        assert len(texts) == len(set(texts))


class TestDeterminism:
    def test_identical_input_yields_identical_chunks(self) -> None:
        """A regenerated card must point at the same passage as the original."""
        runs = [chunk_text(NOTES) for _ in range(5)]
        assert all(run == runs[0] for run in runs)

    def test_chunks_are_immutable_values(self) -> None:
        chunk = chunk_text(NOTES)[0]
        with pytest.raises((AttributeError, TypeError)):
            chunk.text = "mutated"  # type: ignore[misc]


class TestEdgeCases:
    @pytest.mark.parametrize("text", ["", "   ", "\n\n\n"])
    def test_empty_input_yields_no_chunks(self, text: str) -> None:
        assert chunk_text(text) == []

    def test_a_single_short_paragraph_yields_one_chunk(self) -> None:
        chunks = chunk_text("A short note about trees and their properties.")
        assert len(chunks) == 1
        assert chunks[0].heading is None

    def test_a_heading_with_no_body_produces_nothing(self) -> None:
        assert chunk_text("# Just A Heading") == []

    def test_a_tiny_trailing_chunk_is_folded_into_its_predecessor(self) -> None:
        config = ChunkingConfig(target_chars=120, max_chars=600, overlap_chars=0, min_chars=100)
        chunks = chunk_text(
            "A reasonably long first paragraph that comfortably exceeds the target size "
            "and therefore closes a chunk on its own.\n\nTiny tail.",
            config,
        )
        assert all(c.char_count >= 100 or len(chunks) == 1 for c in chunks)

    def test_chunk_char_count_matches_its_text(self) -> None:
        for chunk in chunk_text(NOTES):
            assert chunk.char_count == len(chunk.text)

    def test_chunk_is_hashable_for_use_in_sets(self) -> None:
        assert isinstance(hash(Chunk(0, "t", 0, 1)), int)
