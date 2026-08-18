"""The default provider: no AI at all.

Implemented first, deliberately. Writing the "unavailable" case before any real
integration forces every caller to handle it as a normal state rather than an
error path bolted on later -- which is what keeps StudyForge genuinely usable
with ``AI_PROVIDER=none``.

Every method raises :class:`AIUnavailableError`. Callers catch it and use the
deterministic pipeline, which is not a degraded mode: it is the product.
"""

from __future__ import annotations

from studyforge.ai.base import (
    AIUnavailableError,
    Explanation,
    ExtractedConcepts,
    GeneratedCards,
    GeneratedQuestions,
    ProviderState,
    ProviderStatus,
)

_MESSAGE = (
    "No AI provider is configured. StudyForge's own concept extraction, card "
    "generation and quiz generation are being used instead."
)


class NoAIProvider:
    """A provider that is honestly, explicitly, not there."""

    name = "none"

    async def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            state=ProviderState.DISABLED,
            detail=(
                "AI is switched off. Everything works without it, and nothing leaves this machine."
            ),
        )

    async def generate_flashcards(
        self, *, passage: str, concept: str | None = None, count: int = 5
    ) -> GeneratedCards:
        del passage, concept, count
        raise AIUnavailableError(_MESSAGE)

    async def generate_quiz(self, *, passage: str, count: int = 5) -> GeneratedQuestions:
        del passage, count
        raise AIUnavailableError(_MESSAGE)

    async def explain_answer(
        self, *, question: str, expected_answer: str, passages: list[str]
    ) -> Explanation:
        del question, expected_answer, passages
        raise AIUnavailableError(_MESSAGE)

    async def extract_concepts(self, *, passage: str) -> ExtractedConcepts:
        del passage
        raise AIUnavailableError(_MESSAGE)
