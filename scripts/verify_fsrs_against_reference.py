#!/usr/bin/env python
"""Differentially verify StudyForge's FSRS-6 engine against the reference library.

StudyForge implements FSRS-6 itself rather than depending on a library (see
``docs/STUDY_ENGINE.md`` for why). This script is the evidence that the
implementation is faithful: it drives both StudyForge's scheduler and the
reference ``fsrs`` package through every ordering of four ratings across four
review-gap profiles -- 4,096 review transitions -- and compares stability,
difficulty, state, ladder step and due date at every single step.

The reference package is *not* a project dependency. Run this on demand:

    uv run --with fsrs python scripts/verify_fsrs_against_reference.py

Expected output: ``4096 transitions checked, 0 mismatches``.

The verified outputs are frozen as golden vectors in
``tests/data/fsrs_golden.json``, which the normal test suite asserts against, so
CI catches regressions without needing the reference library installed.
"""

from __future__ import annotations

import itertools
import sys
from datetime import UTC, datetime, timedelta

from studyforge.domain.study.fsrs import Rating, Scheduler, SchedulingCard

try:
    from fsrs import Card as RefCard
    from fsrs import Rating as RefRating
    from fsrs import Scheduler as RefScheduler
except ImportError:  # pragma: no cover - operator-facing guidance
    sys.exit(
        "The reference implementation is not installed.\n"
        "Run this script with:  uv run --with fsrs python "
        "scripts/verify_fsrs_against_reference.py"
    )

START = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
TOLERANCE = 1e-9

# Hours between successive reviews. Chosen to exercise the same-day short-term
# path, ordinary daily study, and multi-year gaps where the curve is flattest.
GAP_PROFILES: list[list[float]] = [
    [0.1, 0.2, 24, 96, 480],
    [24, 24, 24, 24, 24],
    [0.05, 0.05, 0.05, 0.05],
    [72, 240, 1200, 4800, 20000],
]
SEQUENCE_LENGTH = 4


def main() -> int:
    reference = RefScheduler(enable_fuzzing=False)
    ours = Scheduler()
    checked = 0
    mismatches = 0

    for gaps in GAP_PROFILES:
        for ratings in itertools.product([1, 2, 3, 4], repeat=SEQUENCE_LENGTH):
            ref_card, our_card = RefCard(), SchedulingCard.new(due_at=START)
            at = START
            for index, rating in enumerate(ratings):
                at += timedelta(hours=gaps[index])
                ref_card, _ = reference.review_card(ref_card, RefRating(rating), review_datetime=at)
                our_card = ours.review(our_card, Rating(rating), reviewed_at=at).card_after
                checked += 1

                differences = _compare(ref_card, our_card)
                if differences:
                    mismatches += 1
                    print(f"MISMATCH  gaps={gaps}  ratings={ratings[: index + 1]}")
                    for line in differences:
                        print(f"    {line}")
                    break

    print(f"\n{checked} transitions checked, {mismatches} mismatches")
    return 1 if mismatches else 0


def _compare(ref_card: RefCard, our_card: SchedulingCard) -> list[str]:
    differences = []
    pairs: list[tuple[str, object, object]] = [
        ("stability", ref_card.stability, our_card.stability),
        ("difficulty", ref_card.difficulty, our_card.difficulty),
        ("state", ref_card.state.name.lower(), our_card.state.value),
        ("step", ref_card.step, our_card.step),
        ("due", ref_card.due, our_card.due_at),
    ]
    for name, expected, actual in pairs:
        if isinstance(expected, float) and isinstance(actual, float):
            if abs(expected - actual) >= TOLERANCE:
                differences.append(f"{name}: reference={expected!r} ours={actual!r}")
        elif isinstance(expected, datetime) and isinstance(actual, datetime):
            if abs((expected - actual).total_seconds()) >= 1:
                differences.append(f"{name}: reference={expected!r} ours={actual!r}")
        elif expected != actual:
            differences.append(f"{name}: reference={expected!r} ours={actual!r}")
    return differences


if __name__ == "__main__":
    sys.exit(main())
