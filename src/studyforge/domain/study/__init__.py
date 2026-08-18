"""The study engine: FSRS-6 scheduling, queue building and weak-concept analysis.

This package is pure domain logic. It imports nothing from SQLAlchemy, FastAPI
or any AI provider, which is what makes the algorithms exhaustively testable in
isolation -- and what guarantees no language model can reach them.
"""

from studyforge.domain.study.fsrs import (
    DEFAULT_PARAMETERS,
    CardState,
    Rating,
    ReviewSnapshot,
    Scheduler,
    SchedulerConfig,
    SchedulingCard,
)
from studyforge.domain.study.queue import (
    DEFAULT_NEW_CARD_LIMIT,
    DEFAULT_SESSION_LIMIT,
    QueueCandidate,
    QueueEntry,
    QueuePlan,
    QueueReason,
    build_queue,
)
from studyforge.domain.study.weakness import (
    MIN_OBSERVATIONS,
    STATUS_DEFINITIONS,
    ConceptAssessment,
    ConceptStatus,
    Observation,
    ObservationKind,
    assess_concepts,
    observation_window,
    weakest_concepts,
)

__all__ = [
    "DEFAULT_NEW_CARD_LIMIT",
    "DEFAULT_PARAMETERS",
    "DEFAULT_SESSION_LIMIT",
    "MIN_OBSERVATIONS",
    "STATUS_DEFINITIONS",
    "CardState",
    "ConceptAssessment",
    "ConceptStatus",
    "Observation",
    "ObservationKind",
    "QueueCandidate",
    "QueueEntry",
    "QueuePlan",
    "QueueReason",
    "Rating",
    "ReviewSnapshot",
    "Scheduler",
    "SchedulerConfig",
    "SchedulingCard",
    "assess_concepts",
    "build_queue",
    "observation_window",
    "weakest_concepts",
]
