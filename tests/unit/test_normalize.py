"""Text normalisation: each rule in isolation, then combined."""

from __future__ import annotations

import pytest

from studyforge.domain.text.normalize import (
    MINIMUM_MEANINGFUL_CHARS,
    is_probably_meaningful,
    normalize_text,
)


class TestIdempotence:
    """The property everything else depends on.

    Stored chunk offsets point into normalised text. A normaliser that keeps
    changing its own output would silently invalidate every one of them.
    """

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "plain text",
            "hyphen-\nated words here",
            "smart “quotes” and ’apostrophes’",
            "line one\nline two\n\n\n\nline three",
            "tabs\there\tand   spaces",
            "Page 12\n\nContent after a page number",
            "ﬁne ligatures and ​zero width­ soft hyphens",
            "MixedCASE  with\r\nwindows\r\nline endings",
        ],
    )
    def test_normalising_twice_equals_normalising_once(self, raw: str) -> None:
        once = normalize_text(raw)
        assert normalize_text(once) == once


class TestUnicode:
    def test_folds_ligatures(self) -> None:
        assert normalize_text("ﬁnd the ﬂow") == "find the flow"

    def test_folds_smart_quotes_to_ascii(self) -> None:
        assert normalize_text("“quoted” and ’apostrophe’") == "\"quoted\" and 'apostrophe'"

    def test_folds_dashes(self) -> None:
        assert normalize_text("range 1–10 and a break — here") == "range 1-10 and a break - here"

    def test_strips_invisible_characters(self) -> None:
        assert normalize_text("wo​rd­break﻿") == "wordbreak"

    def test_converts_non_breaking_spaces(self) -> None:
        assert normalize_text("a b") == "a b"

    def test_strips_control_characters(self) -> None:
        assert normalize_text("clean\x00text\x07here") == "cleantexthere"

    def test_preserves_non_latin_scripts(self) -> None:
        """Normalisation must not damage material that is not in English."""
        assert "日本語" in normalize_text("Kanji: 日本語 study notes")
        assert "Ω" in normalize_text("Resistance in Ω units")


class TestLineHandling:
    def test_rejoins_words_hyphenated_across_a_line_break(self) -> None:
        assert normalize_text("informa-\ntion theory") == "information theory"

    def test_keeps_genuine_hyphens(self) -> None:
        assert normalize_text("self-balancing tree") == "self-balancing tree"

    def test_joins_hard_wrapped_paragraphs(self) -> None:
        assert normalize_text("The tree is\nbalanced always.") == "The tree is balanced always."

    def test_does_not_join_a_following_capitalised_line(self) -> None:
        """Headings and list items must not be glued onto the previous line."""
        assert normalize_text("Some text\nBinary Trees") == "Some text\nBinary Trees"

    def test_preserves_paragraph_breaks(self) -> None:
        assert normalize_text("First para.\n\nSecond para.") == "First para.\n\nSecond para."

    def test_collapses_runs_of_blank_lines(self) -> None:
        assert normalize_text("a\n\n\n\n\nb") == "a\n\nb"

    def test_normalises_windows_line_endings(self) -> None:
        assert normalize_text("a\r\n\r\nb") == "a\n\nb"

    def test_collapses_repeated_spaces_and_tabs(self) -> None:
        assert normalize_text("a   b\t\tc") == "a b c"

    def test_strips_leading_and_trailing_line_whitespace(self) -> None:
        assert normalize_text("  a  \n  b  ") == "a\nb"


class TestPageArtifacts:
    @pytest.mark.parametrize("artifact", ["12", "Page 12", "Page 12 of 40", "  7  ", "3 / 18"])
    def test_removes_page_numbers_on_their_own_line(self, artifact: str) -> None:
        result = normalize_text(f"Real content here.\n\n{artifact}\n\nMore content.")
        assert "Real content here." in result
        assert "More content." in result
        assert artifact.strip() not in result

    def test_keeps_numbers_that_are_part_of_the_text(self) -> None:
        assert "1962" in normalize_text("The AVL tree was invented in 1962.")

    def test_keeps_a_numbered_list_item(self) -> None:
        assert "1. First step" in normalize_text("1. First step\n2. Second step")


class TestEdgeCases:
    def test_empty_input(self) -> None:
        assert normalize_text("") == ""

    def test_whitespace_only_input(self) -> None:
        assert normalize_text("   \n\n\t  ") == ""

    def test_a_very_long_single_line_is_untouched_in_content(self) -> None:
        line = "word " * 5000
        assert normalize_text(line) == line.strip()


class TestIsProbablyMeaningful:
    def test_accepts_ordinary_prose(self) -> None:
        assert is_probably_meaningful(
            "A binary search tree stores keys in sorted order for fast lookup."
        )

    def test_rejects_text_below_the_length_floor(self) -> None:
        assert not is_probably_meaningful("Too short.")

    def test_rejects_too_few_words(self) -> None:
        assert not is_probably_meaningful("a " * 60)

    def test_rejects_mostly_punctuation(self) -> None:
        """The signature of a broken PDF text layer."""
        assert not is_probably_meaningful("..;;,, //|| ~~~~ ---- **** ????? !!!!! ((())) [[]] %%")

    def test_rejects_empty_and_whitespace(self) -> None:
        assert not is_probably_meaningful("")
        assert not is_probably_meaningful("      ")

    def test_the_threshold_is_configurable(self) -> None:
        text = "Short but genuine words here now."
        assert not is_probably_meaningful(text, minimum_chars=MINIMUM_MEANINGFUL_CHARS)
        assert is_probably_meaningful(text, minimum_chars=10)
