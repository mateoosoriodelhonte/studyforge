"""The AI provider contract, and the schemas its output must satisfy.

Model output is untrusted input
-------------------------------
A language model is a remote service that returns a string. It can return
malformed JSON, invent fields, omit required ones, return an empty list, return
a thousand items, hang, or be switched off entirely. Every one of those is an
ordinary Tuesday, not an exceptional condition, so all of them are handled here
rather than allowed to surface as a 500.

Everything a provider returns is validated with Pydantic before it is allowed
anywhere near the database or a template. Beyond schema validation, a provider
may **never**:

* influence spaced-repetition scheduling in any way
* construct SQL or touch a filesystem path
* make an authorisation decision
* have its output rendered as HTML

Generated material records its provenance -- provider, model, timestamp -- so a
learner can always tell what wrote a card.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field, field_validator

#: Hard ceilings on what a provider may return. A model asked for five cards
#: that returns five hundred is malfunctioning, and accepting them would let a
#: remote service decide how much of the database to fill.
MAX_ITEMS = 25
MAX_TEXT_CHARS = 2_000


class AIUnavailableError(Exception):
    """The provider could not answer.

    Deliberately one exception for every failure mode -- unreachable, timed out,
    rate limited, model missing, malformed response. Callers all do the same
    thing: fall back to the deterministic path and tell the user plainly. The
    human-readable ``message`` is safe to display; ``detail`` is for the log.
    """

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class GeneratedCard(BaseModel):
    """One flashcard proposed by a model."""

    front: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    back: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)

    @field_validator("front", "back")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class GeneratedCards(BaseModel):
    cards: list[GeneratedCard] = Field(default_factory=list, max_length=MAX_ITEMS)


class GeneratedQuestion(BaseModel):
    prompt: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    expected_answer: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    explanation: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)
    choices: list[str] = Field(default_factory=list, max_length=8)
    correct_choice_index: int | None = None

    @field_validator("choices")
    @classmethod
    def _distinct_choices(cls, value: list[str]) -> list[str]:
        cleaned = [choice.strip() for choice in value if choice and choice.strip()]
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("choices must be distinct")
        return cleaned

    @field_validator("correct_choice_index")
    @classmethod
    def _index_is_plausible(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("correct_choice_index cannot be negative")
        return value

    def is_coherent(self) -> bool:
        """Whether this question can actually be presented to a learner.

        Schema validity is not enough: a model will happily return four choices
        and a ``correct_choice_index`` of 7, which passes every field rule and
        is still unusable.
        """
        if not self.choices:
            return self.correct_choice_index is None
        return (
            self.correct_choice_index is not None
            and 0 <= self.correct_choice_index < len(self.choices)
            and len(self.choices) >= 2
        )


class GeneratedQuestions(BaseModel):
    questions: list[GeneratedQuestion] = Field(default_factory=list, max_length=MAX_ITEMS)


class ExtractedConcept(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    definition: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)


class ExtractedConcepts(BaseModel):
    concepts: list[ExtractedConcept] = Field(default_factory=list, max_length=MAX_ITEMS)


class Explanation(BaseModel):
    """A plain-language explanation, grounded in supplied passages."""

    explanation: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    #: Indices into the passages the caller supplied. A model that cites a
    #: passage it was not given is hallucinating, and the caller drops those.
    used_sources: list[int] = Field(default_factory=list, max_length=20)


class ProviderState(enum.StrEnum):
    DISABLED = "disabled"
    READY = "ready"
    UNREACHABLE = "unreachable"
    MODEL_MISSING = "model_missing"


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    """What the settings page shows about the active provider."""

    name: str
    state: ProviderState
    model: str | None = None
    endpoint: str | None = None
    detail: str = ""

    @property
    def is_enabled(self) -> bool:
        return self.state is not ProviderState.DISABLED

    @property
    def is_ready(self) -> bool:
        return self.state is ProviderState.READY


@runtime_checkable
class AIProvider(Protocol):
    """What every provider implements.

    Implementations must never raise anything other than
    :class:`AIUnavailableError` from these methods. Turning a provider's
    idiosyncratic failures into one predictable error is the entire point of
    this boundary.
    """

    name: str

    async def status(self) -> ProviderStatus:
        """Report whether the provider can currently be used. Never raises."""
        ...

    async def generate_flashcards(
        self, *, passage: str, concept: str | None = None, count: int = 5
    ) -> GeneratedCards: ...

    async def generate_quiz(self, *, passage: str, count: int = 5) -> GeneratedQuestions: ...

    async def explain_answer(
        self, *, question: str, expected_answer: str, passages: list[str]
    ) -> Explanation: ...

    async def extract_concepts(self, *, passage: str) -> ExtractedConcepts: ...
