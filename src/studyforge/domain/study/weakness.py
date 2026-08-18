"""Deciding which concepts a learner is struggling with.

This is computed from observed behaviour -- quiz answers and card reviews --
and nothing else. **No language model is involved in this judgement.** An LLM
may later help *explain* a concept the learner finds hard; it never gets to
decide which concepts those are. Classification from behaviour is arithmetic,
and arithmetic should not be delegated to a text generator.

Three commitments shape the design:

**Evidence before judgement.** A concept with two attempts is not "weak" and
not "mastered" -- it is ``NOT_ENOUGH_DATA``. Declaring mastery on a tiny sample
is the most common way learning software lies to people.

**Recency matters.** Something you got wrong twice in January and right five
times last week is not a weak concept. Observations decay with a half-life, so
recent performance dominates.

**Stated definitions.** Every label here has a written meaning
(:data:`STATUS_DEFINITIONS`) that the UI shows the learner. A status nobody can
define is decoration.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

#: Observations below this count cannot support a judgement either way.
MIN_OBSERVATIONS = 4

#: Weight halves every this many days. Two weeks is roughly the horizon over
#: which a learner's grasp of a concept can genuinely change.
RECENCY_HALF_LIFE_DAYS = 14.0

#: Observations older than this are dropped entirely rather than contributing
#: a vanishing weight that only adds noise.
MAX_OBSERVATION_AGE_DAYS = 180

#: Weighted-accuracy thresholds.
STRONG_THRESHOLD = 0.85
NEEDS_WORK_THRESHOLD = 0.60


class ConceptStatus(enum.StrEnum):
    NOT_ENOUGH_DATA = "not_enough_data"
    NEEDS_WORK = "needs_work"
    DEVELOPING = "developing"
    STRONG = "strong"


#: Shown in the UI beside each label, so no status is unexplained.
STATUS_DEFINITIONS: dict[ConceptStatus, str] = {
    ConceptStatus.NOT_ENOUGH_DATA: (
        f"Fewer than {MIN_OBSERVATIONS} recent answers or reviews. Not enough to judge either way."
    ),
    ConceptStatus.NEEDS_WORK: (
        f"Under {NEEDS_WORK_THRESHOLD:.0%} of recent attempts correct, "
        "weighting the last two weeks most heavily."
    ),
    ConceptStatus.DEVELOPING: (
        f"Between {NEEDS_WORK_THRESHOLD:.0%} and {STRONG_THRESHOLD:.0%} of recent attempts correct."
    ),
    ConceptStatus.STRONG: (f"At least {STRONG_THRESHOLD:.0%} of recent attempts correct."),
}


class ObservationKind(enum.StrEnum):
    QUIZ_ANSWER = "quiz_answer"
    CARD_REVIEW = "card_review"


@dataclass(frozen=True, slots=True)
class Observation:
    """One thing the learner did that says something about a concept."""

    concept_id: int
    correct: bool
    occurred_at: datetime
    kind: ObservationKind = ObservationKind.QUIZ_ANSWER


@dataclass(frozen=True, slots=True)
class ConceptAssessment:
    """What the evidence says about one concept.

    ``accuracy`` is ``None`` when there is not enough evidence -- deliberately
    not ``0.0``, because "we do not know" and "they got everything wrong" are
    different facts and a progress screen must not conflate them.
    """

    concept_id: int
    status: ConceptStatus
    accuracy: float | None
    observation_count: int
    incorrect_count: int
    last_seen_at: datetime | None

    @property
    def needs_work(self) -> bool:
        return self.status is ConceptStatus.NEEDS_WORK

    @property
    def has_enough_data(self) -> bool:
        return self.status is not ConceptStatus.NOT_ENOUGH_DATA

    @property
    def status_definition(self) -> str:
        return STATUS_DEFINITIONS[self.status]


def assess_concepts(
    observations: list[Observation], *, now: datetime
) -> dict[int, ConceptAssessment]:
    """Assess every concept that has at least one observation.

    ``now`` is supplied by the caller so the whole dashboard is computed at one
    instant and so tests can pin time.
    """
    grouped: dict[int, list[Observation]] = {}
    for observation in observations:
        age_days = (now - observation.occurred_at).total_seconds() / 86_400
        if age_days > MAX_OBSERVATION_AGE_DAYS:
            continue
        grouped.setdefault(observation.concept_id, []).append(observation)

    return {
        concept_id: _assess_one(concept_id, group, now=now) for concept_id, group in grouped.items()
    }


def _assess_one(
    concept_id: int, observations: list[Observation], *, now: datetime
) -> ConceptAssessment:
    count = len(observations)
    incorrect = sum(1 for o in observations if not o.correct)
    last_seen = max(o.occurred_at for o in observations)

    if count < MIN_OBSERVATIONS:
        return ConceptAssessment(
            concept_id=concept_id,
            status=ConceptStatus.NOT_ENOUGH_DATA,
            accuracy=None,
            observation_count=count,
            incorrect_count=incorrect,
            last_seen_at=last_seen,
        )

    total_weight = 0.0
    correct_weight = 0.0
    for observation in observations:
        weight = _recency_weight(observation.occurred_at, now=now)
        total_weight += weight
        if observation.correct:
            correct_weight += weight

    # Every weight is strictly positive, so total_weight cannot be zero once
    # there is at least one observation; guarded anyway rather than risking a
    # ZeroDivisionError on a progress page.
    accuracy = correct_weight / total_weight if total_weight > 0 else 0.0

    if accuracy >= STRONG_THRESHOLD:
        status = ConceptStatus.STRONG
    elif accuracy < NEEDS_WORK_THRESHOLD:
        status = ConceptStatus.NEEDS_WORK
    else:
        status = ConceptStatus.DEVELOPING

    return ConceptAssessment(
        concept_id=concept_id,
        status=status,
        accuracy=round(accuracy, 4),
        observation_count=count,
        incorrect_count=incorrect,
        last_seen_at=last_seen,
    )


def _recency_weight(occurred_at: datetime, *, now: datetime) -> float:
    """Exponential decay with a fixed half-life, clamped at 1.0.

    A future timestamp (clock skew, or a test being careless) must not produce
    a weight above 1.0 and quietly dominate every other observation.
    """
    age_days = max(0.0, (now - occurred_at).total_seconds() / 86_400)
    return math.pow(0.5, age_days / RECENCY_HALF_LIFE_DAYS)


def weakest_concepts(
    assessments: dict[int, ConceptAssessment], *, limit: int = 10
) -> list[ConceptAssessment]:
    """The concepts most worth working on, worst first.

    Only concepts with enough evidence are eligible: a screen headed "needs
    work" must never include something the learner has barely seen.
    """
    eligible = [
        assessment
        for assessment in assessments.values()
        if assessment.has_enough_data and assessment.status is not ConceptStatus.STRONG
    ]
    eligible.sort(
        key=lambda a: (a.accuracy if a.accuracy is not None else 1.0, -a.observation_count)
    )
    return eligible[:limit]


def observation_window(*, now: datetime) -> datetime:
    """The oldest timestamp still worth loading from the database."""
    return now - timedelta(days=MAX_OBSERVATION_AGE_DAYS)
