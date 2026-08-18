"""Progress metrics for the dashboard and the progress page.

The guiding rule is honesty over impressiveness:

* Rates are ``None`` when there is nothing to divide by. A brand-new install
  shows "no data yet", never a confident-looking ``0%``.
* Every rate is reported alongside the count it was computed from, so a
  learner can see that "100%" came from three answers.
* No composite "mastery score". Combining unrelated quantities into one number
  looks authoritative and means nothing.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from studyforge.domain.study.weakness import (
    ConceptAssessment,
    ConceptStatus,
    assess_concepts,
    weakest_concepts,
)
from studyforge.models import (
    AnswerAttempt,
    Concept,
    Course,
    Flashcard,
    Question,
    Quiz,
    QuizAttempt,
    Review,
)
from studyforge.services.study import gather_observations

#: How far back the activity chart looks.
ACTIVITY_WINDOW_DAYS = 30

#: Reviews and answers newer than this feed the "recent" accuracy figures.
RECENT_WINDOW_DAYS = 30


@dataclass(frozen=True, slots=True)
class Rate:
    """A proportion, and the sample it came from.

    Exists so that no template can render a percentage without also being able
    to show how many observations produced it.
    """

    numerator: int
    denominator: int

    @property
    def value(self) -> float | None:
        """The proportion, or ``None`` when there is nothing to divide by."""
        if self.denominator == 0:
            return None
        return self.numerator / self.denominator

    @property
    def percent(self) -> int | None:
        value = self.value
        return None if value is None else round(value * 100)

    @property
    def has_data(self) -> bool:
        return self.denominator > 0

    def __bool__(self) -> bool:  # pragma: no cover - guards accidental truthiness
        return self.has_data


@dataclass(frozen=True, slots=True)
class DayActivity:
    day: date
    reviews: int
    answers: int

    @property
    def total(self) -> int:
        return self.reviews + self.answers


@dataclass(frozen=True, slots=True)
class ProgressReport:
    """Everything the progress page shows."""

    total_cards: int = 0
    new_cards: int = 0
    suspended_cards: int = 0
    due_now: int = 0
    overdue: int = 0

    reviews_total: int = 0
    reviews_recent: int = 0
    review_recall: Rate = field(default_factory=lambda: Rate(0, 0))
    quiz_accuracy: Rate = field(default_factory=lambda: Rate(0, 0))
    self_graded_answers: int = 0

    concepts_total: int = 0
    status_counts: dict[ConceptStatus, int] = field(default_factory=dict)
    weak_concepts: list[ConceptAssessment] = field(default_factory=list)

    activity: list[DayActivity] = field(default_factory=list)

    @property
    def has_any_activity(self) -> bool:
        return self.reviews_total > 0 or self.quiz_accuracy.has_data

    @property
    def is_empty(self) -> bool:
        return self.total_cards == 0 and self.concepts_total == 0


def build_report(
    session: Session, *, course_id: int | None = None, now: datetime | None = None
) -> ProgressReport:
    """Compute the full progress picture at a single instant.

    ``now`` is taken once and threaded through everything, so no two figures on
    the page can disagree about what "today" means.
    """
    now = now or datetime.now(UTC)
    recent_since = now - timedelta(days=RECENT_WINDOW_DAYS)

    card_filter = [] if course_id is None else [Flashcard.course_id == course_id]

    def count_cards(*conditions: object) -> int:
        return (
            session.scalar(
                select(func.count()).select_from(Flashcard).where(*card_filter, *conditions)  # type: ignore[arg-type]
            )
            or 0
        )

    total_cards = count_cards()
    new_cards = count_cards(Flashcard.stability.is_(None), Flashcard.suspended_at.is_(None))
    suspended = count_cards(Flashcard.suspended_at.is_not(None))
    due_now = count_cards(Flashcard.suspended_at.is_(None), Flashcard.due_at <= now)
    overdue = count_cards(
        Flashcard.suspended_at.is_(None), Flashcard.due_at <= now - timedelta(days=1)
    )

    reviews_query = select(Review)
    if course_id is not None:
        reviews_query = reviews_query.join(Flashcard, Review.flashcard_id == Flashcard.id).where(
            Flashcard.course_id == course_id
        )

    reviews = list(session.scalars(reviews_query))
    recent_reviews = [r for r in reviews if r.reviewed_at >= recent_since]
    review_recall = Rate(
        numerator=sum(1 for r in recent_reviews if r.was_recalled),
        denominator=len(recent_reviews),
    )

    answers_query = select(AnswerAttempt).where(AnswerAttempt.answered_at >= recent_since)
    if course_id is not None:
        answers_query = (
            answers_query.join(Question, AnswerAttempt.question_id == Question.id)
            .join(Quiz, Question.quiz_id == Quiz.id)
            .where(Quiz.course_id == course_id)
        )
    answers = list(session.scalars(answers_query))
    quiz_accuracy = Rate(
        numerator=sum(1 for a in answers if a.is_correct), denominator=len(answers)
    )

    assessments = assess_concepts(
        gather_observations(session, course_id=course_id, now=now), now=now
    )
    status_counts = Counter(a.status for a in assessments.values())
    concepts_total = (
        session.scalar(
            select(func.count())
            .select_from(Concept)
            .where(*([] if course_id is None else [Concept.course_id == course_id]))
        )
        or 0
    )
    # Concepts with no observations at all are still "not enough data"; the
    # assessments dict only contains concepts the learner has actually touched.
    status_counts[ConceptStatus.NOT_ENOUGH_DATA] += max(0, concepts_total - len(assessments))

    return ProgressReport(
        total_cards=total_cards,
        new_cards=new_cards,
        suspended_cards=suspended,
        due_now=due_now,
        overdue=overdue,
        reviews_total=len(reviews),
        reviews_recent=len(recent_reviews),
        review_recall=review_recall,
        quiz_accuracy=quiz_accuracy,
        self_graded_answers=sum(1 for a in answers if a.self_graded),
        concepts_total=concepts_total,
        status_counts=dict(status_counts),
        weak_concepts=weakest_concepts(assessments),
        activity=_activity(reviews, answers, now=now),
    )


def concept_names(session: Session, concept_ids: list[int]) -> dict[int, str]:
    """Look up display names for a set of concept ids, in one query."""
    if not concept_ids:
        return {}
    return {
        concept.id: concept.name
        for concept in session.scalars(select(Concept).where(Concept.id.in_(concept_ids)))
    }


def course_totals(session: Session) -> dict[str, int]:
    """Headline counts for the dashboard, across every non-archived course."""
    now = datetime.now(UTC)
    active_courses = select(Course.id).where(Course.archived_at.is_(None))
    return {
        "courses": session.scalar(select(func.count()).select_from(active_courses.subquery())) or 0,
        "due_now": session.scalar(
            select(func.count())
            .select_from(Flashcard)
            .where(
                Flashcard.suspended_at.is_(None),
                Flashcard.due_at <= now,
                Flashcard.course_id.in_(active_courses),
            )
        )
        or 0,
        "cards": session.scalar(
            select(func.count())
            .select_from(Flashcard)
            .where(Flashcard.course_id.in_(active_courses))
        )
        or 0,
    }


def _activity(
    reviews: list[Review], answers: list[AnswerAttempt], *, now: datetime
) -> list[DayActivity]:
    """Daily counts over the activity window, including days with nothing.

    Zero-days are included deliberately: a chart that silently omits them
    compresses a two-week gap into nothing and makes a lapsed streak look like
    continuous study.
    """
    start = (now - timedelta(days=ACTIVITY_WINDOW_DAYS - 1)).date()
    review_days = Counter(r.reviewed_at.date() for r in reviews if r.reviewed_at.date() >= start)
    answer_days = Counter(a.answered_at.date() for a in answers if a.answered_at.date() >= start)

    return [
        DayActivity(
            day=(day := start + timedelta(days=offset)),
            reviews=review_days.get(day, 0),
            answers=answer_days.get(day, 0),
        )
        for offset in range(ACTIVITY_WINDOW_DAYS)
    ]


def attempts_for_quiz(session: Session, quiz_id: int) -> list[QuizAttempt]:
    return list(
        session.scalars(
            select(QuizAttempt)
            .where(QuizAttempt.quiz_id == quiz_id)
            .order_by(QuizAttempt.started_at.desc())
        )
    )
