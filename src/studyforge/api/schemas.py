"""Pydantic schemas for the JSON API.

Separate from the ORM models on purpose: the API's shape is a contract with
clients, and it should not change just because a column was renamed.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CourseIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=50)
    description: str | None = Field(default=None, max_length=2_000)


class CourseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str | None
    description: str | None
    archived_at: datetime | None
    created_at: datetime


class CourseStatsOut(BaseModel):
    documents: int
    concepts: int
    flashcards: int
    quizzes: int
    due_now: int
    new_cards: int


class CourseDetailOut(CourseOut):
    stats: CourseStatsOut


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    course_id: int
    title: str
    source_type: str
    status: str
    char_count: int
    page_count: int | None
    extraction_error: str | None
    created_at: datetime


class PasteIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1)


class ConceptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    course_id: int
    name: str
    definition: str | None
    extraction_method: str
    score: float
    source_document_id: int | None


class FlashcardIn(BaseModel):
    front: str = Field(min_length=1, max_length=2_000)
    back: str = Field(min_length=1, max_length=2_000)
    concept_id: int | None = None


class FlashcardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    course_id: int
    concept_id: int | None
    front: str
    back: str
    generation_method: str
    source_document_id: int | None
    source_chunk_id: int | None
    state: str
    stability: float | None
    difficulty: float | None
    due_at: datetime
    reps: int
    lapses: int
    suspended_at: datetime | None


class ReviewIn(BaseModel):
    """A rating submitted for a card.

    ``rating`` is constrained to 1-4 at the schema boundary, so an out-of-range
    value is a 422 with a clear message rather than something the scheduler has
    to defend against.
    """

    card_id: int
    rating: int = Field(ge=1, le=4, description="1 Again, 2 Hard, 3 Good, 4 Easy")


class ReviewOut(BaseModel):
    card_id: int
    rating: int
    interval_days: int
    next_due_at: datetime
    state: str
    stability: float | None
    difficulty: float | None
    was_duplicate: bool = Field(
        description="True if this rating was ignored as a repeat submission."
    )


class QueueEntryOut(BaseModel):
    card_id: int
    reason: str
    position: int


class QueueOut(BaseModel):
    entries: list[QueueEntryOut]
    overdue_count: int
    due_count: int
    new_available: int


class RateOut(BaseModel):
    """A proportion together with the sample it came from.

    ``value`` is null when there is no data. Clients must not render 0% for a
    missing measurement, and the schema makes that distinction explicit.
    """

    value: float | None
    numerator: int
    denominator: int


class ConceptStatusOut(BaseModel):
    concept_id: int
    status: str
    status_definition: str
    accuracy: float | None
    observation_count: int


class ProgressOut(BaseModel):
    total_cards: int
    new_cards: int
    suspended_cards: int
    due_now: int
    overdue: int
    reviews_total: int
    review_recall: RateOut
    quiz_accuracy: RateOut
    concepts_total: int
    weak_concepts: list[ConceptStatusOut]


class QuizOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    course_id: int
    title: str
    generation_method: str
    created_at: datetime


class GenerationOut(BaseModel):
    created: int
    skipped_duplicates: int
    note: str | None = None
