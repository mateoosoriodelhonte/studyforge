"""Ollama: local models, no API cost, no data leaving the machine.

Chosen as the one real integration for V1 because it is the only option that is
free in the sense that matters -- not "free tier", not "free for now", but
running on hardware the user already owns, with no account, no key and no
request that leaves localhost.

StudyForge never downloads a model. Pulling several gigabytes because someone
clicked a button would be an appalling default; if the configured model is not
present, the provider says so and the deterministic path continues.

Talks to the ``/api/chat`` endpoint with ``format: "json"``, which asks Ollama
to constrain output to valid JSON. That constraint is treated as a hint, not a
guarantee: the response is still parsed defensively and validated with Pydantic.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx2 as httpx
from pydantic import BaseModel, ValidationError

from studyforge.ai.base import (
    AIUnavailableError,
    Explanation,
    ExtractedConcepts,
    GeneratedCards,
    GeneratedQuestions,
    ProviderState,
    ProviderStatus,
)
from studyforge.logging_config import log_event

logger = logging.getLogger(__name__)

#: Never send more than this much text in one prompt. Bounded so a large
#: document cannot be shipped wholesale to a model, and so a slow local machine
#: is not handed an unbounded job.
MAX_PASSAGE_CHARS = 6_000

_CARD_SCHEMA_HINT = (
    'Respond with JSON only, shaped exactly like: {"cards": [{"front": "...", "back": "..."}]}'
)
_QUIZ_SCHEMA_HINT = (
    "Respond with JSON only, shaped exactly like: "
    '{"questions": [{"prompt": "...", "expected_answer": "...", '
    '"explanation": "...", "choices": ["...", "..."], "correct_choice_index": 0}]}. '
    "Omit choices entirely for a short-answer question."
)
_CONCEPT_SCHEMA_HINT = (
    "Respond with JSON only, shaped exactly like: "
    '{"concepts": [{"name": "...", "definition": "..."}]}'
)
_EXPLANATION_SCHEMA_HINT = (
    'Respond with JSON only, shaped exactly like: {"explanation": "...", "used_sources": [0, 1]}'
)


class OllamaProvider:
    """An AI provider backed by a local Ollama server."""

    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.2",
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        # Injectable so tests exercise every failure mode against a mock
        # transport. CI must never reach a live model.
        self._transport = transport

    # -- status ------------------------------------------------------------

    async def status(self) -> ProviderStatus:
        """Check reachability and whether the model is present. Never raises."""
        base = ProviderStatus(
            name=self.name,
            state=ProviderState.UNREACHABLE,
            model=self._model,
            endpoint=self._base_url,
        )
        try:
            async with self._client() as client:
                response = await client.get("/api/tags", timeout=5.0)
                response.raise_for_status()
                payload = response.json()
        except Exception as error:  # noqa: BLE001 - status must never propagate
            return ProviderStatus(
                name=base.name,
                state=ProviderState.UNREACHABLE,
                model=self._model,
                endpoint=self._base_url,
                detail=(
                    f"Could not reach Ollama at {self._base_url}. "
                    f"Start it with `ollama serve`. ({type(error).__name__})"
                ),
            )

        available = {
            str(entry.get("name", "")).split(":")[0]
            for entry in payload.get("models", [])
            if isinstance(entry, dict)
        }
        if self._model.split(":")[0] not in available:
            return ProviderStatus(
                name=base.name,
                state=ProviderState.MODEL_MISSING,
                model=self._model,
                endpoint=self._base_url,
                detail=(
                    f"Ollama is running but the model {self._model!r} is not "
                    f"installed. Run `ollama pull {self._model}`. StudyForge "
                    "does not download models for you."
                ),
            )

        return ProviderStatus(
            name=base.name,
            state=ProviderState.READY,
            model=self._model,
            endpoint=self._base_url,
            detail=f"Ollama is running {self._model} locally. Nothing leaves this machine.",
        )

    # -- generation --------------------------------------------------------

    async def generate_flashcards(
        self, *, passage: str, concept: str | None = None, count: int = 5
    ) -> GeneratedCards:
        focus = f" Focus on the concept: {concept}." if concept else ""
        return await self._structured(
            system=(
                "You write flashcards from study notes. Use only what the passage "
                "says; never add outside knowledge. One idea per card. If the "
                "passage does not support a good card, return fewer. " + _CARD_SCHEMA_HINT
            ),
            user=f"Write up to {count} flashcards from this passage.{focus}\n\n{passage}",
            model_type=GeneratedCards,
            operation="generate_flashcards",
        )

    async def generate_quiz(self, *, passage: str, count: int = 5) -> GeneratedQuestions:
        result = await self._structured(
            system=(
                "You write quiz questions from study notes. Use only what the "
                "passage says. For multiple choice, every wrong option must be "
                "plausible and drawn from the same subject; never use filler like "
                "'none of the above'. If you cannot write a fair question, write "
                "fewer. " + _QUIZ_SCHEMA_HINT
            ),
            user=f"Write up to {count} questions from this passage.\n\n{passage}",
            model_type=GeneratedQuestions,
            operation="generate_quiz",
        )
        # Schema-valid but unusable questions (an index pointing past the end of
        # the choices) are dropped rather than shown to a learner.
        return GeneratedQuestions(questions=[q for q in result.questions if q.is_coherent()])

    async def extract_concepts(self, *, passage: str) -> ExtractedConcepts:
        return await self._structured(
            system=(
                "You identify the key terms a student must learn from a passage. "
                "Use only terms the passage actually discusses. " + _CONCEPT_SCHEMA_HINT
            ),
            user=f"List the key concepts in this passage.\n\n{passage}",
            model_type=ExtractedConcepts,
            operation="extract_concepts",
        )

    async def explain_answer(
        self, *, question: str, expected_answer: str, passages: list[str]
    ) -> Explanation:
        numbered = "\n\n".join(
            f"[{index}] {text[:MAX_PASSAGE_CHARS]}" for index, text in enumerate(passages)
        )
        result = await self._structured(
            system=(
                "You explain answers to a student using only the numbered sources "
                "provided. If the sources do not support an explanation, say so "
                "plainly rather than inventing one. Cite the sources you used by "
                "their number. " + _EXPLANATION_SCHEMA_HINT
            ),
            user=(
                f"Question: {question}\nExpected answer: {expected_answer}\n\nSources:\n{numbered}"
            ),
            model_type=Explanation,
            operation="explain_answer",
        )
        # A citation to a source we never supplied is a hallucination; drop it
        # rather than rendering a link to nothing.
        valid = [index for index in result.used_sources if 0 <= index < len(passages)]
        return Explanation(explanation=result.explanation, used_sources=valid)

    # -- transport ---------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            transport=self._transport,
            # Ollama is a local process; a proxy configured for the internet has
            # no business intercepting it.
            trust_env=False,
        )

    async def _structured[T: BaseModel](
        self, *, system: str, user: str, model_type: type[T], operation: str
    ) -> T:
        """One request, one validated result, or :class:`AIUnavailableError`."""
        if len(user) > MAX_PASSAGE_CHARS * 2:
            user = user[: MAX_PASSAGE_CHARS * 2]

        log_event(logger, "ai_request_started", provider=self.name, operation=operation)
        raw = await self._chat(system=system, user=user, operation=operation)
        payload = self._parse_json(raw, operation=operation)

        try:
            validated = model_type.model_validate(payload)
        except ValidationError as error:
            log_event(
                logger,
                "ai_request_failed",
                level=logging.WARNING,
                provider=self.name,
                operation=operation,
                reason="schema_mismatch",
                detail=str(error)[:400],
            )
            raise AIUnavailableError(
                "The AI model returned something StudyForge could not use. "
                "Falling back to built-in generation.",
                detail=str(error),
            ) from error

        log_event(logger, "ai_request_completed", provider=self.name, operation=operation)
        return validated

    async def _chat(self, *, system: str, user: str, operation: str) -> str:
        try:
            async with self._client() as client:
                response = await client.post(
                    "/api/chat",
                    json={
                        "model": self._model,
                        "stream": False,
                        "format": "json",
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                    },
                )
        except httpx.TimeoutException as error:
            raise self._failure(
                operation,
                "timeout",
                f"The AI model took longer than {self._timeout:.0f} seconds to respond.",
                error,
            ) from error
        except httpx.HTTPError as error:
            raise self._failure(
                operation,
                "unreachable",
                f"Could not reach Ollama at {self._base_url}. Is it running?",
                error,
            ) from error

        if response.status_code == 404:
            raise self._failure(
                operation,
                "model_missing",
                f"Ollama does not have the model {self._model!r} installed. "
                f"Run `ollama pull {self._model}`.",
                None,
            )
        if response.status_code == 429:
            raise self._failure(
                operation, "rate_limited", "The AI service is rate limiting requests.", None
            )
        if response.status_code >= 400:
            raise self._failure(
                operation,
                "http_error",
                "The AI service returned an error.",
                None,
                status=response.status_code,
            )

        try:
            body: dict[str, Any] = response.json()
        except ValueError as error:
            raise self._failure(
                operation, "invalid_body", "The AI service returned an unreadable response.", error
            ) from error

        content = body.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise self._failure(
                operation, "empty_response", "The AI model returned an empty response.", None
            )
        return content

    def _parse_json(self, raw: str, *, operation: str) -> Any:
        """Parse the model's JSON, tolerating the fences models like to add."""
        text = raw.strip()
        if text.startswith("```"):
            text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise self._failure(
                operation,
                "malformed_json",
                "The AI model did not return valid JSON. Falling back to built-in generation.",
                error,
            ) from error

    def _failure(
        self,
        operation: str,
        reason: str,
        message: str,
        error: Exception | None,
        *,
        status: int | None = None,
    ) -> AIUnavailableError:
        log_event(
            logger,
            "ai_request_failed",
            level=logging.WARNING,
            provider=self.name,
            operation=operation,
            reason=reason,
            status=status,
            detail=str(error)[:200] if error else None,
        )
        return AIUnavailableError(message, detail=str(error) if error else reason)
