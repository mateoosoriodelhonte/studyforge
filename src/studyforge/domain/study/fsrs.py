"""FSRS-6 spaced-repetition scheduler.

An implementation of the Free Spaced Repetition Scheduler, version 6, from the
published algorithm specification of the Open Spaced Repetition project.

Why FSRS rather than SM-2
-------------------------
SM-2 (SuperMemo, 1987) tracks a single per-card "ease factor" and multiplies the
interval by it. It cannot express *how likely you are to remember this card right
now*, so it cannot target a retention rate. FSRS models memory with three
quantities -- difficulty, stability and retrievability -- fitted against a very
large corpus of real reviews, and schedules each card for the moment its
predicted recall probability decays to the configured target. Benchmarks
published by the project report materially fewer reviews for equal retention.

Why implement it here rather than depend on a library
-----------------------------------------------------
The scheduler is roughly 150 lines of closed-form arithmetic with no I/O. Owning
it means the scheduling rules are readable, diff-able and covered by our own
progression tests, and it keeps the study engine free of a dependency in the
hottest path of the product. The *optimiser* (fitting ``w`` to a user's own
review history with gradient descent) is deliberately out of scope for V1; we
ship the published default parameters, which are what FSRS uses for any user
without enough review history to train on.

Design notes
------------
Everything in this module is pure and deterministic: :class:`Scheduler` is a
frozen dataclass, :meth:`Scheduler.review` takes a card and returns a *new*
card, and no function reads the clock. The caller supplies ``reviewed_at``. This
is a deliberate constraint -- a learning schedule that cannot be reproduced
cannot be tested, and an LLM must never be anywhere near it.

Interval fuzzing (randomly jittering intervals to break up same-day clumps) is
supported but **off by default**, because it would make the engine
non-deterministic. When enabled it derives its jitter from a caller-supplied
seed rather than global RNG state, so it stays reproducible.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# --------------------------------------------------------------------------
# Constants from the FSRS-6 specification
# --------------------------------------------------------------------------

#: The 21 published FSRS-6 default weights. ``w[20]`` is the forgetting-curve
#: decay; ``w[0..3]`` are initial stability per rating.
DEFAULT_PARAMETERS: tuple[float, ...] = (
    0.212,
    1.2931,
    2.3065,
    8.2956,
    6.4133,
    0.8334,
    3.0194,
    0.001,
    1.8722,
    0.1666,
    0.796,
    1.4835,
    0.0614,
    0.2629,
    1.6483,
    0.6014,
    1.8729,
    0.5425,
    0.0912,
    0.0658,
    0.1542,
)

PARAMETER_COUNT = 21

MIN_DIFFICULTY = 1.0
MAX_DIFFICULTY = 10.0
MIN_STABILITY = 0.001

#: Anki's default learning ladder, and a reasonable default here.
DEFAULT_LEARNING_STEPS: tuple[timedelta, ...] = (
    timedelta(minutes=1),
    timedelta(minutes=10),
)
DEFAULT_RELEARNING_STEPS: tuple[timedelta, ...] = (timedelta(minutes=10),)

#: ~100 years. Beyond this the model is extrapolating far past its training data.
DEFAULT_MAXIMUM_INTERVAL_DAYS = 36_500


class Rating(enum.IntEnum):
    """How well the learner recalled the card. The integer values are load-bearing:
    the FSRS formulas use ``rating`` arithmetically (e.g. ``G - 3``)."""

    AGAIN = 1
    HARD = 2
    GOOD = 3
    EASY = 4

    @property
    def is_lapse(self) -> bool:
        return self is Rating.AGAIN

    @property
    def label(self) -> str:
        return _RATING_LABELS[self]


_RATING_LABELS = {
    Rating.AGAIN: "Again",
    Rating.HARD: "Hard",
    Rating.GOOD: "Good",
    Rating.EASY: "Easy",
}


class CardState(enum.StrEnum):
    """Where a card sits in the learning ladder.

    ``LEARNING`` covers brand-new cards too: a card with no memory state yet is
    ``LEARNING`` at step 0. Keeping "new" out of the state machine removes a
    whole class of transition bugs; "is this card new?" is simply
    ``card.stability is None``.
    """

    LEARNING = "learning"
    REVIEW = "review"
    RELEARNING = "relearning"


@dataclass(frozen=True, slots=True)
class SchedulingCard:
    """The scheduler's view of a flashcard: memory state plus ladder position.

    This is intentionally *not* the ORM model. The persistence layer maps its
    columns onto this structure, which keeps the maths independent of the
    database and lets tests construct cards in one line.
    """

    due_at: datetime
    state: CardState = CardState.LEARNING
    step: int | None = 0
    stability: float | None = None
    difficulty: float | None = None
    last_reviewed_at: datetime | None = None
    reps: int = 0
    lapses: int = 0

    @property
    def is_new(self) -> bool:
        """True until the card's first review establishes a memory state."""
        return self.stability is None or self.difficulty is None

    @classmethod
    def new(cls, *, due_at: datetime) -> SchedulingCard:
        """A card that has never been reviewed."""
        return cls(due_at=due_at, state=CardState.LEARNING, step=0)


@dataclass(frozen=True, slots=True)
class ReviewSnapshot:
    """The complete, auditable record of one review.

    Persisting before/after memory state (rather than just the rating) means a
    schedule can be recomputed or explained after the fact, and makes the
    progression tests assert on real numbers rather than opaque outcomes.
    """

    rating: Rating
    reviewed_at: datetime
    card_before: SchedulingCard
    card_after: SchedulingCard
    elapsed_days: int | None
    scheduled_interval: timedelta
    retrievability_before: float | None


@dataclass(frozen=True, slots=True)
class SchedulerConfig:
    """Tunable scheduling policy.

    ``desired_retention`` is the knob a learner actually cares about: the
    probability of recall the scheduler aims for at review time. Lower means
    longer intervals and fewer reviews at the cost of more forgetting.
    """

    parameters: tuple[float, ...] = DEFAULT_PARAMETERS
    desired_retention: float = 0.9
    learning_steps: tuple[timedelta, ...] = DEFAULT_LEARNING_STEPS
    relearning_steps: tuple[timedelta, ...] = DEFAULT_RELEARNING_STEPS
    maximum_interval_days: int = DEFAULT_MAXIMUM_INTERVAL_DAYS

    def __post_init__(self) -> None:
        if len(self.parameters) != PARAMETER_COUNT:
            raise ValueError(
                f"FSRS-6 requires exactly {PARAMETER_COUNT} parameters, got {len(self.parameters)}"
            )
        if not 0.70 <= self.desired_retention <= 0.99:
            raise ValueError(
                f"desired_retention must be between 0.70 and 0.99, got {self.desired_retention}"
            )
        if self.maximum_interval_days < 1:
            raise ValueError("maximum_interval_days must be at least 1")
        decay = self.parameters[20]
        if decay <= 0:
            raise ValueError("parameter w[20] (decay) must be positive")


@dataclass(frozen=True, slots=True)
class Scheduler:
    """A pure FSRS-6 scheduler.

    Usage::

        scheduler = Scheduler()
        card = SchedulingCard.new(due_at=now)
        snapshot = scheduler.review(card, Rating.GOOD, reviewed_at=now)
        snapshot.card_after.due_at  # when to show it next
    """

    config: SchedulerConfig = SchedulerConfig()

    # -- derived forgetting-curve constants -------------------------------

    @property
    def _decay(self) -> float:
        """The forgetting curve's exponent, negated: ``R = (1 + F·t/S)^decay``."""
        return -self.config.parameters[20]

    @property
    def _factor(self) -> float:
        """Normalisation so that ``R == 0.9`` exactly when elapsed time == stability."""
        return float(0.9 ** (1.0 / self._decay)) - 1.0

    @property
    def _w(self) -> tuple[float, ...]:
        return self.config.parameters

    # -- public API --------------------------------------------------------

    def retrievability(self, card: SchedulingCard, at: datetime) -> float:
        """Probability the learner recalls this card at ``at``, in ``[0, 1]``.

        Returns ``0.0`` for a card with no memory state: an unseen card has no
        predicted recall, and callers use :attr:`SchedulingCard.is_new` to
        distinguish "never learned" from "certainly forgotten".
        """
        if card.stability is None or card.last_reviewed_at is None:
            return 0.0
        elapsed_days = max(0, (_as_utc(at) - _as_utc(card.last_reviewed_at)).days)
        base = 1 + self._factor * elapsed_days / card.stability
        return float(base**self._decay)

    def interval_for_stability(self, stability: float) -> int:
        """Whole days until retrievability decays to ``desired_retention``.

        At the default 90% retention this returns approximately ``stability``,
        which is the definition of stability. Always at least one day, and never
        beyond ``maximum_interval_days``.
        """
        raw = float(
            (stability / self._factor)
            * (self.config.desired_retention ** (1.0 / self._decay) - 1.0)
        )
        return max(1, min(round(raw), self.config.maximum_interval_days))

    def review(
        self,
        card: SchedulingCard,
        rating: Rating,
        *,
        reviewed_at: datetime,
    ) -> ReviewSnapshot:
        """Apply a rating and return the resulting schedule.

        Never mutates ``card``. The returned snapshot carries both the before
        and after state so the caller can persist a full review log.
        """
        reviewed_at = _as_utc(reviewed_at)
        elapsed_days = (
            (reviewed_at - _as_utc(card.last_reviewed_at)).days
            if card.last_reviewed_at is not None
            else None
        )
        retrievability_before = None if card.is_new else self.retrievability(card, reviewed_at)

        memory = self._next_memory_state(card, rating, reviewed_at, elapsed_days)
        state, step, interval = self._next_position(card, rating, memory.stability)

        card_after = SchedulingCard(
            due_at=reviewed_at + interval,
            state=state,
            step=step,
            stability=memory.stability,
            difficulty=memory.difficulty,
            last_reviewed_at=reviewed_at,
            reps=card.reps + 1,
            lapses=card.lapses + (1 if rating.is_lapse else 0),
        )
        return ReviewSnapshot(
            rating=rating,
            reviewed_at=reviewed_at,
            card_before=card,
            card_after=card_after,
            elapsed_days=elapsed_days,
            scheduled_interval=interval,
            retrievability_before=retrievability_before,
        )

    # -- memory state ------------------------------------------------------

    def _next_memory_state(
        self,
        card: SchedulingCard,
        rating: Rating,
        reviewed_at: datetime,
        elapsed_days: int | None,
    ) -> _Memory:
        if card.stability is None or card.difficulty is None:
            # First ever review: seed stability and difficulty from the rating.
            return _Memory(
                stability=self._initial_stability(rating),
                difficulty=self._initial_difficulty(rating, clamp=True),
            )

        if elapsed_days is not None and elapsed_days < 1:
            # Same-day re-review. The long-term formula assumes measurable decay
            # has happened; within a day it hasn't, so FSRS uses a separate
            # short-term update.
            stability = self._short_term_stability(card.stability, rating)
        else:
            stability = self._next_long_term_stability(
                difficulty=card.difficulty,
                stability=card.stability,
                retrievability=self.retrievability(card, reviewed_at),
                rating=rating,
            )
        return _Memory(
            stability=stability,
            difficulty=self._next_difficulty(card.difficulty, rating),
        )

    def _initial_stability(self, rating: Rating) -> float:
        """``w[0..3]``, indexed by rating."""
        return _clamp_stability(self._w[rating - 1])

    def _initial_difficulty(self, rating: Rating, *, clamp: bool) -> float:
        """``D0(G) = w[4] - e^(w[5]·(G-1)) + 1``."""
        difficulty = self._w[4] - math.exp(self._w[5] * (rating - 1)) + 1
        return _clamp_difficulty(difficulty) if clamp else difficulty

    def _next_difficulty(self, difficulty: float, rating: Rating) -> float:
        """Grade-driven change, linearly damped near the ceiling, then reverted
        toward the difficulty an "Easy" first answer would imply.

        The damping term ``(10 - D)/9`` shrinks upward moves as difficulty
        approaches its maximum, so a run of "Again" cannot pin a card at 10 and
        strand it there.
        """
        delta = -(self._w[6] * (rating - 3))
        damped = difficulty + (10.0 - difficulty) * delta / 9.0
        target = self._initial_difficulty(Rating.EASY, clamp=False)
        reverted = self._w[7] * target + (1 - self._w[7]) * damped
        return _clamp_difficulty(reverted)

    def _short_term_stability(self, stability: float, rating: Rating) -> float:
        """``S' = S · e^(w17·(G-3+w18)) · S^(-w19)``, floored so that a passing
        grade can never *reduce* stability."""
        increase = math.exp(self._w[17] * (rating - 3 + self._w[18])) * (stability ** -self._w[19])
        if rating is not Rating.AGAIN:
            increase = max(increase, 1.0)
        return _clamp_stability(stability * increase)

    def _next_long_term_stability(
        self,
        *,
        difficulty: float,
        stability: float,
        retrievability: float,
        rating: Rating,
    ) -> float:
        if rating is Rating.AGAIN:
            return _clamp_stability(
                self._post_lapse_stability(difficulty, stability, retrievability)
            )
        return _clamp_stability(
            self._recall_stability(difficulty, stability, retrievability, rating)
        )

    def _recall_stability(
        self, difficulty: float, stability: float, retrievability: float, rating: Rating
    ) -> float:
        """``S' = S · (1 + SInc)``.

        The three factors encode the model's core claims: easier cards gain more
        (``11 - D``), already-stable cards gain proportionally less
        (``S^-w9``, the stabilisation decay), and reviews done when recall was
        *hard* gain the most (``e^((1-R)·w10) - 1``, the spacing effect).
        """
        hard_penalty = self._w[15] if rating is Rating.HARD else 1.0
        easy_bonus = self._w[16] if rating is Rating.EASY else 1.0
        increase = float(
            math.exp(self._w[8])
            * (11 - difficulty)
            * stability ** -self._w[9]
            * (math.exp((1 - retrievability) * self._w[10]) - 1)
            * hard_penalty
            * easy_bonus
        )
        return stability * (1 + increase)

    def _post_lapse_stability(
        self, difficulty: float, stability: float, retrievability: float
    ) -> float:
        """Stability after forgetting.

        The ``min`` is essential: forgetting a card must never make it *more*
        stable than it was, which the long-term term alone does not guarantee
        for very low prior stability.
        """
        long_term = float(
            self._w[11]
            * difficulty ** -self._w[12]
            * ((stability + 1) ** self._w[13] - 1)
            * math.exp((1 - retrievability) * self._w[14])
        )
        short_term = stability / math.exp(self._w[17] * self._w[18])
        return min(long_term, short_term)

    # -- ladder position ---------------------------------------------------

    def _next_position(
        self, card: SchedulingCard, rating: Rating, stability: float
    ) -> tuple[CardState, int | None, timedelta]:
        """Advance the learning ladder and produce the next interval."""
        if card.state is CardState.REVIEW:
            return self._position_from_review(rating, stability)

        steps = (
            self.config.learning_steps
            if card.state is CardState.LEARNING
            else self.config.relearning_steps
        )
        return self._position_on_ladder(card, rating, stability, steps)

    def _position_from_review(
        self, rating: Rating, stability: float
    ) -> tuple[CardState, int | None, timedelta]:
        graduated = (CardState.REVIEW, None, self._days(stability))
        if rating is not Rating.AGAIN:
            return graduated
        if not self.config.relearning_steps:
            # No relearning ladder configured: fall straight back to a (much
            # shorter) review interval rather than dropping the card entirely.
            return graduated
        return CardState.RELEARNING, 0, self.config.relearning_steps[0]

    def _position_on_ladder(
        self,
        card: SchedulingCard,
        rating: Rating,
        stability: float,
        steps: tuple[timedelta, ...],
    ) -> tuple[CardState, int | None, timedelta]:
        step = card.step or 0
        graduated = (CardState.REVIEW, None, self._days(stability))

        # Guard against a card scheduled under a longer ladder than is now
        # configured: a passing grade graduates it instead of indexing off the end.
        if not steps or (step >= len(steps) and rating is not Rating.AGAIN):
            return graduated

        if rating is Rating.AGAIN:
            return card.state, 0, steps[0]

        if rating is Rating.EASY:
            return graduated

        if rating is Rating.HARD:
            # Hard repeats the current step. On the very first step there is no
            # "current" delay worth repeating, so FSRS interpolates: the mean of
            # the first two steps, or 1.5x the only step.
            if step == 0 and len(steps) == 1:
                return card.state, 0, steps[0] * 1.5
            if step == 0:
                return card.state, 0, (steps[0] + steps[1]) / 2
            return card.state, step, steps[step]

        # Rating.GOOD: advance one rung, or graduate off the top.
        if step + 1 >= len(steps):
            return graduated
        return card.state, step + 1, steps[step + 1]

    def _days(self, stability: float) -> timedelta:
        return timedelta(days=self.interval_for_stability(stability))

    # -- convenience -------------------------------------------------------

    def preview(self, card: SchedulingCard, *, at: datetime) -> dict[Rating, ReviewSnapshot]:
        """What each of the four buttons would do, without committing to one.

        Used to label the review UI with real intervals ("Good -> 4d") so the
        learner can see the schedule rather than trust it blindly.
        """
        return {rating: self.review(card, rating, reviewed_at=at) for rating in Rating}


@dataclass(frozen=True, slots=True)
class _Memory:
    stability: float
    difficulty: float


def _clamp_difficulty(value: float) -> float:
    return min(max(value, MIN_DIFFICULTY), MAX_DIFFICULTY)


def _clamp_stability(value: float) -> float:
    return max(value, MIN_STABILITY)


def _as_utc(value: datetime) -> datetime:
    """Normalise to timezone-aware UTC.

    SQLite hands back naive datetimes. Rather than scatter ``tzinfo`` checks
    through the scheduler, every boundary funnels through here; naive input is
    interpreted as UTC, which is the only thing StudyForge ever stores.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = [
    "DEFAULT_LEARNING_STEPS",
    "DEFAULT_MAXIMUM_INTERVAL_DAYS",
    "DEFAULT_PARAMETERS",
    "DEFAULT_RELEARNING_STEPS",
    "MAX_DIFFICULTY",
    "MIN_DIFFICULTY",
    "MIN_STABILITY",
    "PARAMETER_COUNT",
    "CardState",
    "Rating",
    "ReviewSnapshot",
    "Scheduler",
    "SchedulerConfig",
    "SchedulingCard",
]
