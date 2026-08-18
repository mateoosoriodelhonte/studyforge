"""Tests for the FSRS-6 scheduler.

The golden vectors in ``tests/data/fsrs_golden.json`` were produced by this
implementation *after* it was verified against the reference ``fsrs`` package
(open-spaced-repetition/py-fsrs) with a differential test over 4,096 review
transitions -- every ordering of four ratings across four different review-gap
profiles -- with zero mismatches in stability, difficulty, state, ladder step
and due date. They exist to catch regressions without taking a runtime
dependency on the reference implementation.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta, timezone
from itertools import pairwise
from pathlib import Path
from typing import ClassVar

import pytest

from studyforge.domain.study.fsrs import (
    DEFAULT_PARAMETERS,
    MAX_DIFFICULTY,
    MIN_DIFFICULTY,
    MIN_STABILITY,
    PARAMETER_COUNT,
    CardState,
    Rating,
    Scheduler,
    SchedulerConfig,
    SchedulingCard,
)

START = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
GOLDEN = json.loads((Path(__file__).parent.parent / "data" / "fsrs_golden.json").read_text())


@pytest.fixture
def scheduler() -> Scheduler:
    return Scheduler()


def drill(
    scheduler: Scheduler,
    ratings: list[Rating],
    *,
    gap: timedelta = timedelta(days=1),
    start: datetime = START,
) -> SchedulingCard:
    """Review a fresh card through ``ratings``, spaced ``gap`` apart."""
    card = SchedulingCard.new(due_at=start)
    at = start
    for rating in ratings:
        card = scheduler.review(card, rating, reviewed_at=at).card_after
        at += gap
    return card


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestSchedulerConfig:
    def test_ships_the_21_published_parameters(self) -> None:
        assert len(DEFAULT_PARAMETERS) == PARAMETER_COUNT

    @pytest.mark.parametrize("count", [0, 17, 20, 22])
    def test_rejects_wrong_parameter_count(self, count: int) -> None:
        with pytest.raises(ValueError, match="exactly 21 parameters"):
            SchedulerConfig(parameters=tuple([0.5] * count))

    @pytest.mark.parametrize("retention", [0.0, 0.5, 0.69, 1.0, 1.5, -0.1])
    def test_rejects_out_of_range_retention(self, retention: float) -> None:
        with pytest.raises(ValueError, match="desired_retention"):
            SchedulerConfig(desired_retention=retention)

    @pytest.mark.parametrize("retention", [0.70, 0.85, 0.9, 0.99])
    def test_accepts_sensible_retention(self, retention: float) -> None:
        assert SchedulerConfig(desired_retention=retention).desired_retention == retention

    def test_rejects_non_positive_decay(self) -> None:
        with pytest.raises(ValueError, match="decay"):
            SchedulerConfig(parameters=(*DEFAULT_PARAMETERS[:20], 0.0))

    def test_rejects_zero_maximum_interval(self) -> None:
        with pytest.raises(ValueError, match="maximum_interval_days"):
            SchedulerConfig(maximum_interval_days=0)


# ---------------------------------------------------------------------------
# Forgetting curve
# ---------------------------------------------------------------------------


class TestRetrievability:
    def test_new_card_has_no_retrievability(self, scheduler: Scheduler) -> None:
        assert scheduler.retrievability(SchedulingCard.new(due_at=START), START) == 0.0

    def test_is_exactly_90_percent_when_elapsed_equals_stability(
        self, scheduler: Scheduler
    ) -> None:
        # This identity is the definition of stability and pins _factor/_decay.
        card = SchedulingCard(due_at=START, stability=10.0, difficulty=5.0, last_reviewed_at=START)
        assert scheduler.retrievability(card, START + timedelta(days=10)) == pytest.approx(
            0.9, abs=1e-9
        )

    def test_is_one_at_the_moment_of_review(self, scheduler: Scheduler) -> None:
        card = SchedulingCard(due_at=START, stability=10.0, difficulty=5.0, last_reviewed_at=START)
        assert scheduler.retrievability(card, START) == pytest.approx(1.0)

    def test_decays_monotonically(self, scheduler: Scheduler) -> None:
        card = SchedulingCard(due_at=START, stability=10.0, difficulty=5.0, last_reviewed_at=START)
        values = [
            scheduler.retrievability(card, START + timedelta(days=d)) for d in range(0, 400, 7)
        ]
        assert all(earlier >= later for earlier, later in pairwise(values))

    def test_stays_within_probability_bounds_over_a_century(self, scheduler: Scheduler) -> None:
        card = SchedulingCard(due_at=START, stability=0.5, difficulty=9.0, last_reviewed_at=START)
        for days in (0, 1, 10, 365, 3650, 36500):
            r = scheduler.retrievability(card, START + timedelta(days=days))
            assert 0.0 <= r <= 1.0

    def test_a_clock_that_runs_backwards_does_not_exceed_one(self, scheduler: Scheduler) -> None:
        # Negative elapsed time is clamped to zero rather than producing R > 1.
        card = SchedulingCard(due_at=START, stability=10.0, difficulty=5.0, last_reviewed_at=START)
        assert scheduler.retrievability(card, START - timedelta(days=5)) == pytest.approx(1.0)

    def test_higher_stability_means_better_retention_at_equal_delay(
        self, scheduler: Scheduler
    ) -> None:
        weak = SchedulingCard(due_at=START, stability=2.0, difficulty=5.0, last_reviewed_at=START)
        strong = SchedulingCard(
            due_at=START, stability=50.0, difficulty=5.0, last_reviewed_at=START
        )
        at = START + timedelta(days=10)
        assert scheduler.retrievability(strong, at) > scheduler.retrievability(weak, at)


# ---------------------------------------------------------------------------
# Interval derivation
# ---------------------------------------------------------------------------


class TestIntervals:
    def test_at_90_percent_retention_interval_tracks_stability(self, scheduler: Scheduler) -> None:
        for stability in (1.0, 7.0, 30.0, 365.0):
            assert scheduler.interval_for_stability(stability) == pytest.approx(
                round(stability), abs=1
            )

    def test_never_schedules_less_than_one_day(self, scheduler: Scheduler) -> None:
        assert scheduler.interval_for_stability(MIN_STABILITY) == 1
        assert scheduler.interval_for_stability(0.01) == 1

    def test_clamps_to_the_maximum_interval(self) -> None:
        scheduler = Scheduler(SchedulerConfig(maximum_interval_days=365))
        assert scheduler.interval_for_stability(10_000.0) == 365

    def test_lower_retention_target_buys_longer_intervals(self) -> None:
        strict = Scheduler(SchedulerConfig(desired_retention=0.97))
        relaxed = Scheduler(SchedulerConfig(desired_retention=0.75))
        assert relaxed.interval_for_stability(50.0) > strict.interval_for_stability(50.0)

    def test_interval_is_monotonic_in_stability(self, scheduler: Scheduler) -> None:
        intervals = [scheduler.interval_for_stability(s) for s in range(1, 500, 13)]
        assert all(a <= b for a, b in pairwise(intervals))


# ---------------------------------------------------------------------------
# Ratings and memory-state progression
# ---------------------------------------------------------------------------


class TestFirstReview:
    @pytest.mark.parametrize("rating", list(Rating))
    def test_seeds_stability_from_the_rating(self, scheduler: Scheduler, rating: Rating) -> None:
        card = drill(scheduler, [rating])
        assert card.stability == pytest.approx(DEFAULT_PARAMETERS[rating - 1])

    def test_better_first_answers_seed_more_stability(self, scheduler: Scheduler) -> None:
        stabilities = [drill(scheduler, [r]).stability for r in Rating]
        assert all(a < b for a, b in pairwise(stabilities))  # type: ignore[arg-type]

    def test_better_first_answers_seed_less_difficulty(self, scheduler: Scheduler) -> None:
        difficulties = [drill(scheduler, [r]).difficulty for r in Rating]
        assert all(a > b for a, b in pairwise(difficulties))  # type: ignore[arg-type]

    def test_records_a_lapse_only_for_again(self, scheduler: Scheduler) -> None:
        assert drill(scheduler, [Rating.AGAIN]).lapses == 1
        for rating in (Rating.HARD, Rating.GOOD, Rating.EASY):
            assert drill(scheduler, [rating]).lapses == 0

    def test_counts_the_repetition(self, scheduler: Scheduler) -> None:
        assert drill(scheduler, [Rating.GOOD, Rating.GOOD, Rating.AGAIN]).reps == 3


class TestDifficulty:
    def test_stays_within_bounds_under_relentless_failure(self, scheduler: Scheduler) -> None:
        card = drill(scheduler, [Rating.AGAIN] * 50)
        assert MIN_DIFFICULTY <= card.difficulty <= MAX_DIFFICULTY  # type: ignore[operator]

    def test_stays_within_bounds_under_relentless_success(self, scheduler: Scheduler) -> None:
        card = drill(scheduler, [Rating.EASY] * 50)
        assert MIN_DIFFICULTY <= card.difficulty <= MAX_DIFFICULTY  # type: ignore[operator]

    def test_again_raises_difficulty_and_easy_lowers_it(self, scheduler: Scheduler) -> None:
        base = drill(scheduler, [Rating.GOOD, Rating.GOOD])
        assert base.difficulty is not None
        harder = scheduler.review(base, Rating.AGAIN, reviewed_at=START + timedelta(days=5))
        easier = scheduler.review(base, Rating.EASY, reviewed_at=START + timedelta(days=5))
        assert harder.card_after.difficulty > base.difficulty  # type: ignore[operator]
        assert easier.card_after.difficulty < base.difficulty  # type: ignore[operator]

    def test_linear_damping_keeps_a_maxed_card_recoverable(self, scheduler: Scheduler) -> None:
        """A card pinned near difficulty 10 must still respond to good answers."""
        wrecked = drill(scheduler, [Rating.AGAIN] * 30)
        assert wrecked.difficulty is not None
        recovered = drill(scheduler, [Rating.AGAIN] * 30 + [Rating.EASY] * 10)
        assert recovered.difficulty < wrecked.difficulty  # type: ignore[operator]


class TestStability:
    def test_never_drops_below_the_floor(self, scheduler: Scheduler) -> None:
        card = drill(scheduler, [Rating.AGAIN] * 40, gap=timedelta(minutes=1))
        assert card.stability >= MIN_STABILITY  # type: ignore[operator]

    def test_never_shrinks_when_the_learner_keeps_passing(self, scheduler: Scheduler) -> None:
        """Across the learning ladder and into review, passing grades are safe.

        Growth is not *strict* everywhere: FSRS floors the same-day stability
        multiplier at 1.0, so a card re-passed minutes later holds its stability
        rather than gaining. What must never happen is a decrease.
        """
        card = SchedulingCard.new(due_at=START)
        at = START
        seen: list[float] = []
        for _ in range(6):
            card = scheduler.review(card, Rating.GOOD, reviewed_at=at).card_after
            assert card.stability is not None
            seen.append(card.stability)
            at = card.due_at
        assert all(a <= b for a, b in pairwise(seen))

    def test_grows_strictly_once_reviews_are_days_apart(self, scheduler: Scheduler) -> None:
        card = drill(scheduler, [Rating.EASY])  # graduate straight to review
        seen: list[float] = []
        for _ in range(6):
            card = scheduler.review(card, Rating.GOOD, reviewed_at=card.due_at).card_after
            assert card.stability is not None
            seen.append(card.stability)
        assert all(a < b for a, b in pairwise(seen))

    def test_forgetting_never_increases_stability(self, scheduler: Scheduler) -> None:
        card = drill(scheduler, [Rating.GOOD] * 4, gap=timedelta(days=10))
        assert card.stability is not None
        lapsed = scheduler.review(card, Rating.AGAIN, reviewed_at=START + timedelta(days=60))
        assert lapsed.card_after.stability < card.stability  # type: ignore[operator]

    def test_easy_beats_good_beats_hard(self, scheduler: Scheduler) -> None:
        base = drill(scheduler, [Rating.GOOD], gap=timedelta(days=1))
        at = START + timedelta(days=10)
        outcomes = {
            r: scheduler.review(base, r, reviewed_at=at).card_after.stability for r in Rating
        }
        assert (
            outcomes[Rating.AGAIN]
            < outcomes[Rating.HARD]
            < outcomes[Rating.GOOD]
            < outcomes[Rating.EASY]
        )  # type: ignore[operator]

    def test_spacing_effect_reviewing_later_gains_more(self, scheduler: Scheduler) -> None:
        """Recalling something you'd nearly forgotten strengthens it more."""
        base = drill(scheduler, [Rating.GOOD] * 2, gap=timedelta(days=1))
        soon = scheduler.review(base, Rating.GOOD, reviewed_at=START + timedelta(days=3))
        late = scheduler.review(base, Rating.GOOD, reviewed_at=START + timedelta(days=40))
        assert late.card_after.stability > soon.card_after.stability  # type: ignore[operator]

    def test_same_day_passing_review_cannot_lose_stability(self, scheduler: Scheduler) -> None:
        card = drill(scheduler, [Rating.GOOD])
        assert card.stability is not None
        for rating in (Rating.HARD, Rating.GOOD, Rating.EASY):
            after = scheduler.review(
                card, rating, reviewed_at=START + timedelta(minutes=10)
            ).card_after
            assert after.stability >= card.stability  # type: ignore[operator]


# ---------------------------------------------------------------------------
# Learning ladder / state machine
# ---------------------------------------------------------------------------


class TestLearningLadder:
    def test_new_cards_start_in_learning_at_step_zero(self) -> None:
        card = SchedulingCard.new(due_at=START)
        assert card.state is CardState.LEARNING
        assert card.step == 0
        assert card.is_new

    def test_good_walks_up_the_ladder_then_graduates(self, scheduler: Scheduler) -> None:
        card = SchedulingCard.new(due_at=START)
        card = scheduler.review(card, Rating.GOOD, reviewed_at=START).card_after
        assert (card.state, card.step) == (CardState.LEARNING, 1)
        card = scheduler.review(card, Rating.GOOD, reviewed_at=card.due_at).card_after
        assert (card.state, card.step) == (CardState.REVIEW, None)

    def test_easy_graduates_immediately(self, scheduler: Scheduler) -> None:
        card = drill(scheduler, [Rating.EASY])
        assert card.state is CardState.REVIEW
        assert card.step is None

    def test_again_resets_to_the_first_step(self, scheduler: Scheduler) -> None:
        card = drill(scheduler, [Rating.GOOD, Rating.AGAIN], gap=timedelta(minutes=1))
        assert (card.state, card.step) == (CardState.LEARNING, 0)

    def test_hard_repeats_the_current_step(self, scheduler: Scheduler) -> None:
        card = drill(scheduler, [Rating.GOOD, Rating.HARD], gap=timedelta(minutes=1))
        assert (card.state, card.step) == (CardState.LEARNING, 1)

    def test_hard_on_first_step_interpolates_between_the_first_two(
        self, scheduler: Scheduler
    ) -> None:
        snap = scheduler.review(SchedulingCard.new(due_at=START), Rating.HARD, reviewed_at=START)
        expected = (timedelta(minutes=1) + timedelta(minutes=10)) / 2
        assert snap.scheduled_interval == expected

    def test_hard_on_a_single_step_ladder_uses_one_and_a_half_times(self) -> None:
        scheduler = Scheduler(SchedulerConfig(learning_steps=(timedelta(minutes=10),)))
        snap = scheduler.review(SchedulingCard.new(due_at=START), Rating.HARD, reviewed_at=START)
        assert snap.scheduled_interval == timedelta(minutes=15)

    def test_empty_ladder_graduates_on_the_first_review(self) -> None:
        scheduler = Scheduler(SchedulerConfig(learning_steps=()))
        card = scheduler.review(
            SchedulingCard.new(due_at=START), Rating.GOOD, reviewed_at=START
        ).card_after
        assert card.state is CardState.REVIEW
        assert card.due_at >= START + timedelta(days=1)

    def test_a_card_stranded_past_the_end_of_a_shortened_ladder_graduates(self) -> None:
        """Guards the case where a card was scheduled under a longer ladder."""
        scheduler = Scheduler(SchedulerConfig(learning_steps=(timedelta(minutes=1),)))
        stranded = SchedulingCard(
            due_at=START,
            state=CardState.LEARNING,
            step=5,
            stability=3.0,
            difficulty=5.0,
            last_reviewed_at=START - timedelta(days=1),
        )
        after = scheduler.review(stranded, Rating.GOOD, reviewed_at=START).card_after
        assert after.state is CardState.REVIEW
        assert after.step is None


class TestRelearning:
    def test_forgetting_a_review_card_drops_it_into_relearning(self, scheduler: Scheduler) -> None:
        card = drill(scheduler, [Rating.EASY], gap=timedelta(days=1))
        assert card.state is CardState.REVIEW
        lapsed = scheduler.review(
            card, Rating.AGAIN, reviewed_at=START + timedelta(days=30)
        ).card_after
        assert (lapsed.state, lapsed.step) == (CardState.RELEARNING, 0)
        assert lapsed.lapses == 1

    def test_relearning_graduates_back_to_review(self, scheduler: Scheduler) -> None:
        card = drill(scheduler, [Rating.EASY])
        card = scheduler.review(
            card, Rating.AGAIN, reviewed_at=START + timedelta(days=30)
        ).card_after
        card = scheduler.review(card, Rating.GOOD, reviewed_at=card.due_at).card_after
        assert card.state is CardState.REVIEW

    def test_without_a_relearning_ladder_a_lapse_stays_in_review(self) -> None:
        scheduler = Scheduler(SchedulerConfig(relearning_steps=()))
        card = drill(scheduler, [Rating.EASY])
        lapsed = scheduler.review(
            card, Rating.AGAIN, reviewed_at=START + timedelta(days=30)
        ).card_after
        assert lapsed.state is CardState.REVIEW
        assert lapsed.lapses == 1

    def test_a_lapse_shortens_the_next_interval_dramatically(self, scheduler: Scheduler) -> None:
        card = drill(scheduler, [Rating.GOOD] * 4, gap=timedelta(days=15))
        before = (card.due_at - START).days
        lapsed = scheduler.review(card, Rating.AGAIN, reviewed_at=card.due_at).card_after
        assert (lapsed.due_at - card.due_at) < timedelta(days=1)
        assert before > 1


# ---------------------------------------------------------------------------
# Purity, timezones, snapshots
# ---------------------------------------------------------------------------


class TestPurityAndBoundaries:
    def test_review_does_not_mutate_the_input_card(self, scheduler: Scheduler) -> None:
        card = SchedulingCard.new(due_at=START)
        scheduler.review(card, Rating.EASY, reviewed_at=START)
        assert card.stability is None
        assert card.reps == 0
        assert card.state is CardState.LEARNING

    def test_is_deterministic_across_repeated_runs(self, scheduler: Scheduler) -> None:
        runs = [
            drill(scheduler, [Rating.GOOD, Rating.AGAIN, Rating.HARD, Rating.EASY])
            for _ in range(5)
        ]
        assert all(r == runs[0] for r in runs)

    def test_naive_datetimes_are_interpreted_as_utc(self, scheduler: Scheduler) -> None:
        """SQLite returns naive datetimes; the engine must not blow up on them."""
        naive = datetime(2026, 1, 1, 12, 0)
        aware = scheduler.review(SchedulingCard.new(due_at=START), Rating.GOOD, reviewed_at=START)
        from_naive = scheduler.review(
            SchedulingCard.new(due_at=naive), Rating.GOOD, reviewed_at=naive
        )
        assert from_naive.card_after.stability == aware.card_after.stability
        assert from_naive.card_after.due_at == aware.card_after.due_at

    def test_non_utc_timezones_are_normalised(self, scheduler: Scheduler) -> None:
        tokyo = datetime(2026, 1, 1, 21, 0, tzinfo=timezone(timedelta(hours=9)))
        assert tokyo == START
        assert (
            scheduler.review(
                SchedulingCard.new(due_at=START), Rating.GOOD, reviewed_at=tokyo
            ).card_after.due_at
            == scheduler.review(
                SchedulingCard.new(due_at=START), Rating.GOOD, reviewed_at=START
            ).card_after.due_at
        )

    def test_snapshot_captures_before_and_after(self, scheduler: Scheduler) -> None:
        card = drill(scheduler, [Rating.GOOD, Rating.GOOD])
        snap = scheduler.review(card, Rating.HARD, reviewed_at=START + timedelta(days=9))
        assert snap.card_before is card
        assert snap.card_after is not card
        assert snap.rating is Rating.HARD
        assert snap.elapsed_days == 8
        assert snap.retrievability_before is not None
        assert 0.0 <= snap.retrievability_before <= 1.0

    def test_first_review_snapshot_has_no_prior_retrievability(self, scheduler: Scheduler) -> None:
        snap = scheduler.review(SchedulingCard.new(due_at=START), Rating.GOOD, reviewed_at=START)
        assert snap.retrievability_before is None
        assert snap.elapsed_days is None

    def test_all_state_stays_finite_over_a_long_random_but_fixed_history(
        self, scheduler: Scheduler
    ) -> None:
        # A fixed pseudo-random pattern; no RNG, so this is reproducible.
        pattern = [Rating((i * 7 + i // 3) % 4 + 1) for i in range(200)]
        card = SchedulingCard.new(due_at=START)
        at = START
        for rating in pattern:
            card = scheduler.review(card, rating, reviewed_at=at).card_after
            at = card.due_at
            assert card.stability is not None and math.isfinite(card.stability)
            assert card.difficulty is not None and math.isfinite(card.difficulty)
            assert MIN_DIFFICULTY <= card.difficulty <= MAX_DIFFICULTY
            assert card.stability >= MIN_STABILITY

    def test_preview_shows_all_four_outcomes_without_committing(self, scheduler: Scheduler) -> None:
        card = drill(scheduler, [Rating.GOOD, Rating.GOOD])
        options = scheduler.preview(card, at=START + timedelta(days=8))
        assert set(options) == set(Rating)
        assert card.reps == 2  # untouched
        due = [options[r].card_after.due_at for r in Rating]
        assert due == sorted(due), "better ratings must not schedule sooner"


class TestRating:
    def test_only_again_is_a_lapse(self) -> None:
        assert Rating.AGAIN.is_lapse
        assert not any(r.is_lapse for r in (Rating.HARD, Rating.GOOD, Rating.EASY))

    def test_labels_are_stable(self) -> None:
        assert [r.label for r in Rating] == ["Again", "Hard", "Good", "Easy"]

    def test_values_are_load_bearing_for_the_formulas(self) -> None:
        assert (Rating.AGAIN, Rating.HARD, Rating.GOOD, Rating.EASY) == (1, 2, 3, 4)


# ---------------------------------------------------------------------------
# Golden vectors (regression lock, verified against reference py-fsrs)
# ---------------------------------------------------------------------------


class TestGoldenVectors:
    GAPS: ClassVar[dict[str, list[float]]] = {
        "all_good_daily": [0, 24, 96, 240, 600],
        "struggling": [0, 0.1, 0.2, 24, 48, 24],
        "easy_streak": [0, 24, 240, 2400],
        "lapse_recovery": [0, 24, 240, 720, 0.2, 48],
        "hard_grind": [0, 0.3, 24, 48, 96],
    }

    @pytest.mark.parametrize("name", sorted(GOLDEN))
    def test_matches_reference_verified_vectors(self, scheduler: Scheduler, name: str) -> None:
        card = SchedulingCard.new(due_at=START)
        at = START
        for expected, gap_hours in zip(GOLDEN[name], self.GAPS[name], strict=True):
            at += timedelta(hours=gap_hours)
            snap = scheduler.review(card, Rating(expected["rating"]), reviewed_at=at)
            card = snap.card_after
            assert card.stability == pytest.approx(expected["stability"], abs=1e-6)
            assert card.difficulty == pytest.approx(expected["difficulty"], abs=1e-6)
            assert card.state.value == expected["state"]
            assert card.step == expected["step"]
            assert snap.scheduled_interval.days == expected["interval_days"]
            assert card.reps == expected["reps"]
            assert card.lapses == expected["lapses"]
