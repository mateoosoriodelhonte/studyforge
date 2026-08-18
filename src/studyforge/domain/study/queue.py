"""Building today's study queue.

The product promise is that a learner presses one button and studies the right
thing. That makes queue order a real design decision rather than an
implementation detail, so it lives here as a pure function over value objects
and is covered by its own tests.

Priority, highest first:

1. **Overdue reviews**, most overdue first. These are actively being forgotten;
   every day they wait costs more.
2. **Due reviews**, earliest due first.
3. **Cards for weak concepts**, even if not yet due. Bringing these forward is
   the point of tracking weakness at all.
4. **New cards**, up to a separate daily allowance.

New cards are capped separately because they are the one part of the queue that
compounds: every new card introduced today becomes review debt tomorrow. An
uncapped queue on a freshly imported document would bury the learner.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta

#: Default ceiling on one session, so a large backlog is faced in portions.
DEFAULT_SESSION_LIMIT = 60

#: Default ceiling on brand-new cards per session.
DEFAULT_NEW_CARD_LIMIT = 15


class QueueReason(enum.StrEnum):
    """Why a card is in the queue. Shown in the UI, so the order is legible."""

    OVERDUE = "overdue"
    DUE = "due"
    WEAK_CONCEPT = "weak_concept"
    NEW = "new"


#: Sort order of the reasons.
_PRIORITY = {
    QueueReason.OVERDUE: 0,
    QueueReason.DUE: 1,
    QueueReason.WEAK_CONCEPT: 2,
    QueueReason.NEW: 3,
}


@dataclass(frozen=True, slots=True)
class QueueCandidate:
    """The queue builder's view of a flashcard."""

    card_id: int
    due_at: datetime
    is_new: bool
    concept_id: int | None = None
    suspended: bool = False


@dataclass(frozen=True, slots=True)
class QueueEntry:
    """A card selected for study, and the reason it was selected."""

    card_id: int
    reason: QueueReason
    position: int


@dataclass(frozen=True, slots=True)
class QueuePlan:
    """The built queue, plus the counts a dashboard wants."""

    entries: list[QueueEntry]
    overdue_count: int
    due_count: int
    new_available: int

    @property
    def is_empty(self) -> bool:
        return not self.entries

    @property
    def size(self) -> int:
        return len(self.entries)

    def counts_by_reason(self) -> dict[QueueReason, int]:
        counts = dict.fromkeys(QueueReason, 0)
        for entry in self.entries:
            counts[entry.reason] += 1
        return counts


def build_queue(
    candidates: list[QueueCandidate],
    *,
    now: datetime,
    weak_concept_ids: frozenset[int] = frozenset(),
    session_limit: int = DEFAULT_SESSION_LIMIT,
    new_card_limit: int = DEFAULT_NEW_CARD_LIMIT,
    overdue_after: timedelta = timedelta(days=1),
) -> QueuePlan:
    """Select and order the cards to study now.

    Deterministic: ties break on ``card_id`` so the same inputs always produce
    the same queue in the same order.
    """
    session_limit = max(0, session_limit)
    new_card_limit = max(0, new_card_limit)

    active = [c for c in candidates if not c.suspended]
    overdue: list[QueueCandidate] = []
    due: list[QueueCandidate] = []
    weak: list[QueueCandidate] = []
    new: list[QueueCandidate] = []

    for candidate in active:
        if candidate.is_new:
            new.append(candidate)
        elif candidate.due_at <= now - overdue_after:
            overdue.append(candidate)
        elif candidate.due_at <= now:
            due.append(candidate)
        elif candidate.concept_id is not None and candidate.concept_id in weak_concept_ids:
            # Not due yet, but the learner is demonstrably struggling with it.
            weak.append(candidate)

    overdue.sort(key=lambda c: (c.due_at, c.card_id))
    due.sort(key=lambda c: (c.due_at, c.card_id))
    weak.sort(key=lambda c: (c.due_at, c.card_id))
    new.sort(key=lambda c: c.card_id)

    entries: list[QueueEntry] = []

    def take(group: list[QueueCandidate], reason: QueueReason, limit: int | None = None) -> None:
        allowance = session_limit - len(entries)
        if limit is not None:
            allowance = min(allowance, limit)
        for candidate in group[: max(0, allowance)]:
            entries.append(
                QueueEntry(card_id=candidate.card_id, reason=reason, position=len(entries))
            )

    take(overdue, QueueReason.OVERDUE)
    take(due, QueueReason.DUE)
    take(weak, QueueReason.WEAK_CONCEPT)
    take(new, QueueReason.NEW, limit=new_card_limit)

    return QueuePlan(
        entries=entries,
        overdue_count=len(overdue),
        due_count=len(due),
        new_available=len(new),
    )


__all__ = [
    "DEFAULT_NEW_CARD_LIMIT",
    "DEFAULT_SESSION_LIMIT",
    "QueueCandidate",
    "QueueEntry",
    "QueuePlan",
    "QueueReason",
    "build_queue",
]
