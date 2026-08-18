"""Ask My Notes: retrieval-grounded question answering.

Two halves, kept strictly apart.

**Retrieval** searches the learner's own chunks with FTS5 and always runs. It is
genuinely useful on its own -- "show me the passages in my notes about AVL
rotations" is a real answer, and it is the answer StudyForge gives when no AI is
configured.

**Generation** is optional. When a provider is available it is asked to explain
*using only the retrieved passages*, and is instructed to decline when they do
not support an answer. The response is validated, its citations are checked
against the passages actually supplied, and the UI renders retrieved evidence
visually distinct from generated prose.

Two rules follow from that separation:

* Only retrieved chunks are ever sent to a provider. Never a whole document,
  never another course's material, never configuration.
* An answer with no surviving citations is presented as unsupported rather than
  as an answer, because a grounded explanation that cites nothing is not
  grounded.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from studyforge.ai.base import AIProvider, AIUnavailableError
from studyforge.logging_config import log_event
from studyforge.services import search as search_service

logger = logging.getLogger(__name__)

#: How many passages are retrieved, and therefore the most that can be sent to a
#: provider. Small on purpose: more context is not more grounding, and every
#: extra passage is more of the learner's private material leaving the process.
MAX_PASSAGES = 5

#: Longest passage sent to a provider. Chunks are already bounded, but this is
#: the belt-and-braces limit on prompt size.
MAX_PASSAGE_CHARS = 1_500


@dataclass(frozen=True, slots=True)
class Passage:
    """One retrieved chunk, with enough context to cite it."""

    chunk_id: int
    text: str
    heading: str | None
    document_id: int
    document_title: str
    course_id: int

    @property
    def citation(self) -> str:
        return f"{self.document_title} — {self.heading}" if self.heading else self.document_title


@dataclass(frozen=True, slots=True)
class Answer:
    """The result of asking a question.

    ``explanation`` is ``None`` whenever no model produced one -- because none
    is configured, because it failed, or because it declined. ``passages`` is
    populated either way: retrieval is the part that always works.
    """

    question: str
    passages: list[Passage] = field(default_factory=list)
    explanation: str | None = None
    cited: list[Passage] = field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    unavailable_reason: str | None = None

    @property
    def has_evidence(self) -> bool:
        return bool(self.passages)

    @property
    def has_explanation(self) -> bool:
        return bool(self.explanation)

    @property
    def is_grounded(self) -> bool:
        """An explanation is only grounded if it actually cites the evidence."""
        return self.has_explanation and bool(self.cited)


async def ask(
    session: Session,
    provider: AIProvider,
    *,
    question: str,
    course_id: int | None = None,
) -> Answer:
    """Answer a question from the learner's own notes."""
    cleaned = (question or "").strip()
    if not cleaned:
        return Answer(question="")

    passages = _retrieve(session, cleaned, course_id=course_id)
    if not passages:
        log_event(logger, "ask_no_evidence", course_id=course_id)
        return Answer(question=cleaned)

    status = await provider.status()
    if not status.is_ready:
        # No AI, or it is not reachable. The passages are still the answer.
        return Answer(
            question=cleaned,
            passages=passages,
            unavailable_reason=status.detail or None,
            provider=status.name,
        )

    # Only the retrieved passages leave this process. Nothing else.
    texts = [passage.text[:MAX_PASSAGE_CHARS] for passage in passages]
    try:
        explanation = await provider.explain_answer(
            question=cleaned,
            expected_answer="(answer from the passages provided)",
            passages=texts,
        )
    except AIUnavailableError as error:
        log_event(
            logger,
            "ask_generation_failed",
            level=logging.WARNING,
            provider=status.name,
            reason=error.detail,
        )
        return Answer(
            question=cleaned,
            passages=passages,
            unavailable_reason=error.message,
            provider=status.name,
        )

    cited = [passages[index] for index in explanation.used_sources if index < len(passages)]
    log_event(
        logger,
        "ask_answered",
        provider=status.name,
        passages=len(passages),
        cited=len(cited),
    )
    return Answer(
        question=cleaned,
        passages=passages,
        explanation=explanation.explanation,
        cited=cited,
        provider=status.name,
        model=status.model,
    )


def _retrieve(session: Session, question: str, *, course_id: int | None) -> list[Passage]:
    rows = search_service.retrieve_chunks(
        session, question, course_id=course_id, limit=MAX_PASSAGES
    )
    return [
        Passage(
            chunk_id=row.chunk_id,
            text=row.text,
            heading=row.heading,
            document_id=row.document_id,
            document_title=row.document_title,
            course_id=row.course_id,
        )
        for row in rows
    ]
