"""Study sessions: building the queue and recording reviews."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from studyforge.domain.study.fsrs import Rating, Scheduler
from studyforge.domain.study.queue import (
    DEFAULT_NEW_CARD_LIMIT,
    DEFAULT_SESSION_LIMIT,
    QueueCandidate,
    QueuePlan,
    QueueReason,
    build_queue,
)
from studyforge.domain.study.weakness import (
    Observation,
    ObservationKind,
    assess_concepts,
    observation_window,
    weakest_concepts,
)
from studyforge.logging_config import log_event
from studyforge.models import (
    AnswerAttempt,
    Concept,
    Flashcard,
    Review,
    StudySession,
)
from studyforge.services.exceptions import ConflictError, NotFoundError

logger = logging.getLogger(__name__)

#: One scheduler instance for the whole application. It is a frozen dataclass
#: with no state, so sharing it is safe and avoids rebuilding it per review.
SCHEDULER = Scheduler()


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    """What happened when a card was rated."""

    card: Flashcard
    rating: Rating
    interval_days: int
    next_due_at: datetime
    was_duplicate: bool = False


def start_session(
    session: Session, *, course_id: int | None = None, now: datetime | None = None
) -> StudySession:
    study_session = StudySession(course_id=course_id, started_at=now or datetime.now(UTC))
    session.add(study_session)
    session.flush()
    log_event(logger, "study_session_started", session_id=study_session.id, course_id=course_id)
    return study_session


def end_session(session: Session, session_id: int, *, now: datetime | None = None) -> StudySession:
    study_session = get_session(session, session_id)
    if study_session.ended_at is None:
        study_session.ended_at = now or datetime.now(UTC)
        session.flush()
        log_event(
            logger,
            "study_session_completed",
            session_id=session_id,
            cards_reviewed=study_session.cards_reviewed,
            again_count=study_session.again_count,
        )
    return study_session


def get_session(session: Session, session_id: int) -> StudySession:
    study_session = session.get(StudySession, session_id)
    if study_session is None:
        raise NotFoundError("That study session does not exist.")
    return study_session


def build_study_queue(
    session: Session,
    *,
    course_id: int | None = None,
    now: datetime | None = None,
    session_limit: int = DEFAULT_SESSION_LIMIT,
    new_card_limit: int = DEFAULT_NEW_CARD_LIMIT,
) -> QueuePlan:
    """Decide what to study now.

    Weak concepts are computed first so their cards can be pulled forward even
    when not yet due -- which is the entire point of tracking weakness.
    """
    now = now or datetime.now(UTC)

    query = select(Flashcard).where(Flashcard.suspended_at.is_(None))
    if course_id is not None:
        query = query.where(Flashcard.course_id == course_id)

    candidates = [
        QueueCandidate(
            card_id=card.id,
            due_at=card.due_at,
            is_new=card.is_new,
            concept_id=card.concept_id,
            suspended=False,
        )
        for card in session.scalars(query)
    ]

    weak_ids = frozenset(
        assessment.concept_id
        for assessment in weakest_concepts(
            assess_concepts(gather_observations(session, course_id=course_id, now=now), now=now)
        )
    )

    return build_queue(
        candidates,
        now=now,
        weak_concept_ids=weak_ids,
        session_limit=session_limit,
        new_card_limit=new_card_limit,
    )


def record_review(
    session: Session,
    *,
    card_id: int,
    rating: Rating,
    study_session_id: int | None = None,
    now: datetime | None = None,
    duration_ms: int | None = None,
) -> ReviewOutcome:
    """Apply a rating to a card and persist the resulting schedule.

    Guards against double submission: a browser retry, an impatient second
    click or a duplicated HTMX request must not review the same card twice and
    corrupt its interval. If the card was already reviewed at this instant, the
    existing schedule is returned unchanged.
    """
    now = now or datetime.now(UTC)
    card = session.get(Flashcard, card_id)
    if card is None:
        raise NotFoundError("That flashcard does not exist. It may have been deleted.")
    if card.suspended_at is not None:
        raise ConflictError("That card is suspended, so it cannot be reviewed.")

    if card.last_reviewed_at is not None and card.last_reviewed_at >= now:
        log_event(logger, "review_duplicate_ignored", card_id=card_id, level=logging.WARNING)
        return ReviewOutcome(
            card=card,
            rating=rating,
            interval_days=max(0, (card.due_at - now).days),
            next_due_at=card.due_at,
            was_duplicate=True,
        )

    before = card.to_scheduling_card()
    snapshot = SCHEDULER.review(before, rating, reviewed_at=now)
    card.apply_scheduling(snapshot.card_after)

    session.add(
        Review(
            flashcard_id=card.id,
            study_session_id=study_session_id,
            rating=int(rating),
            reviewed_at=snapshot.reviewed_at,
            elapsed_days=snapshot.elapsed_days,
            scheduled_days=snapshot.scheduled_interval.days,
            state_before=before.state,
            state_after=snapshot.card_after.state,
            stability_before=before.stability,
            stability_after=snapshot.card_after.stability,
            difficulty_before=before.difficulty,
            difficulty_after=snapshot.card_after.difficulty,
            retrievability_before=snapshot.retrievability_before,
            duration_ms=duration_ms,
        )
    )

    if study_session_id is not None:
        study_session = session.get(StudySession, study_session_id)
        if study_session is not None:
            study_session.cards_reviewed += 1
            if rating.is_lapse:
                study_session.again_count += 1

    session.flush()
    log_event(
        logger,
        "review_completed",
        card_id=card.id,
        rating=int(rating),
        interval_days=snapshot.scheduled_interval.days,
        state=card.state.value,
    )
    return ReviewOutcome(
        card=card,
        rating=rating,
        interval_days=snapshot.scheduled_interval.days,
        next_due_at=card.due_at,
    )


def preview_intervals(card: Flashcard, *, now: datetime | None = None) -> dict[Rating, str]:
    """Human-readable interval each button would produce.

    Labelling the buttons with real intervals is a deliberate honesty choice:
    the learner can see the schedule rather than being asked to trust it.
    """
    now = now or datetime.now(UTC)
    previews = SCHEDULER.preview(card.to_scheduling_card(), at=now)
    return {
        rating: _humanise(snapshot.scheduled_interval.total_seconds())
        for rating, snapshot in previews.items()
    }


def _humanise(seconds: float) -> str:
    minutes = seconds / 60
    if minutes < 60:
        return f"{max(1, round(minutes))}m"
    hours = minutes / 60
    if hours < 24:
        return f"{round(hours)}h"
    days = hours / 24
    if days < 30:
        return f"{round(days)}d"
    if days < 365:
        return f"{days / 30.44:.1f}mo".replace(".0", "")
    return f"{days / 365.25:.1f}y".replace(".0", "")


def gather_observations(
    session: Session, *, course_id: int | None, now: datetime
) -> list[Observation]:
    """Gather the behavioural evidence the weakness engine judges on.

    Both quiz answers and card reviews count: getting a question wrong and
    pressing *Again* on a card are two ways of saying the same thing.
    """
    since = observation_window(now=now)
    observations: list[Observation] = []

    answers = select(AnswerAttempt).where(
        AnswerAttempt.concept_id.is_not(None), AnswerAttempt.answered_at >= since
    )
    reviews = (
        select(Review, Flashcard.concept_id)
        .join(Flashcard, Review.flashcard_id == Flashcard.id)
        .where(Flashcard.concept_id.is_not(None), Review.reviewed_at >= since)
    )
    if course_id is not None:
        concept_ids = select(Concept.id).where(Concept.course_id == course_id)
        answers = answers.where(AnswerAttempt.concept_id.in_(concept_ids))
        reviews = reviews.where(Flashcard.course_id == course_id)

    for answer in session.scalars(answers):
        assert answer.concept_id is not None
        observations.append(
            Observation(
                concept_id=answer.concept_id,
                correct=answer.is_correct,
                occurred_at=answer.answered_at,
                kind=ObservationKind.QUIZ_ANSWER,
            )
        )

    for review, concept_id in session.execute(reviews):
        observations.append(
            Observation(
                concept_id=concept_id,
                correct=review.was_recalled,
                occurred_at=review.reviewed_at,
                kind=ObservationKind.CARD_REVIEW,
            )
        )

    return observations


def queue_cards(session: Session, plan: QueuePlan) -> list[tuple[Flashcard, QueueReason]]:
    """Load the cards for a plan, preserving queue order.

    One query, then re-ordered in Python: the plan is already bounded to a
    session's worth of cards, so ordering in SQL would add a CASE expression
    for no measurable gain.
    """
    if plan.is_empty:
        return []
    by_id = {
        card.id: card
        for card in session.scalars(
            select(Flashcard).where(Flashcard.id.in_([e.card_id for e in plan.entries]))
        )
    }
    return [
        (by_id[entry.card_id], entry.reason) for entry in plan.entries if entry.card_id in by_id
    ]
