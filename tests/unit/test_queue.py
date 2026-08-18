"""The study queue: priority order, limits and determinism."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from studyforge.domain.study.queue import (
    QueueCandidate,
    QueueReason,
    build_queue,
)

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def card(
    card_id: int,
    *,
    days_overdue: float = 0,
    is_new: bool = False,
    concept_id: int | None = None,
    suspended: bool = False,
) -> QueueCandidate:
    return QueueCandidate(
        card_id=card_id,
        due_at=NOW - timedelta(days=days_overdue),
        is_new=is_new,
        concept_id=concept_id,
        suspended=suspended,
    )


def reasons(plan: object) -> list[QueueReason]:
    return [entry.reason for entry in plan.entries]  # type: ignore[attr-defined]


class TestPriorityOrder:
    def test_overdue_comes_before_due_before_weak_before_new(self) -> None:
        plan = build_queue(
            [
                card(1, is_new=True),
                card(2, days_overdue=-5, concept_id=7),  # future, weak concept
                card(3, days_overdue=0.1),  # due today
                card(4, days_overdue=10),  # overdue
            ],
            now=NOW,
            weak_concept_ids=frozenset({7}),
        )
        assert reasons(plan) == [
            QueueReason.OVERDUE,
            QueueReason.DUE,
            QueueReason.WEAK_CONCEPT,
            QueueReason.NEW,
        ]
        assert [e.card_id for e in plan.entries] == [4, 3, 2, 1]

    def test_the_most_overdue_card_comes_first(self) -> None:
        plan = build_queue(
            [card(1, days_overdue=2), card(2, days_overdue=30), card(3, days_overdue=9)],
            now=NOW,
        )
        assert [e.card_id for e in plan.entries] == [2, 3, 1]

    def test_positions_are_contiguous(self) -> None:
        plan = build_queue([card(i, days_overdue=i) for i in range(1, 6)], now=NOW)
        assert [e.position for e in plan.entries] == [0, 1, 2, 3, 4]


class TestWeakConcepts:
    def test_a_weak_concept_card_is_pulled_forward_before_it_is_due(self) -> None:
        """Pulling these forward is the entire point of tracking weakness."""
        plan = build_queue(
            [card(1, days_overdue=-30, concept_id=42)],
            now=NOW,
            weak_concept_ids=frozenset({42}),
        )
        assert reasons(plan) == [QueueReason.WEAK_CONCEPT]

    def test_a_healthy_concept_card_is_left_alone_until_due(self) -> None:
        plan = build_queue([card(1, days_overdue=-30, concept_id=42)], now=NOW)
        assert plan.is_empty

    def test_a_card_with_no_concept_is_never_pulled_forward(self) -> None:
        plan = build_queue(
            [card(1, days_overdue=-30, concept_id=None)],
            now=NOW,
            weak_concept_ids=frozenset({42}),
        )
        assert plan.is_empty


class TestLimits:
    def test_the_session_limit_is_respected(self) -> None:
        plan = build_queue([card(i, days_overdue=5) for i in range(100)], now=NOW, session_limit=20)
        assert plan.size == 20
        assert plan.overdue_count == 100, "the full backlog is still reported"

    def test_new_cards_have_their_own_cap(self) -> None:
        """New cards compound: each one is review debt tomorrow."""
        plan = build_queue(
            [card(i, is_new=True) for i in range(100)],
            now=NOW,
            session_limit=60,
            new_card_limit=5,
        )
        assert plan.size == 5
        assert plan.new_available == 100

    def test_reviews_are_never_crowded_out_by_new_cards(self) -> None:
        plan = build_queue(
            [card(i, days_overdue=3) for i in range(10)]
            + [card(100 + i, is_new=True) for i in range(10)],
            now=NOW,
            session_limit=12,
            new_card_limit=10,
        )
        counts = plan.counts_by_reason()
        assert counts[QueueReason.OVERDUE] == 10
        assert counts[QueueReason.NEW] == 2

    @pytest.mark.parametrize("limit", [0, -1])
    def test_a_zero_limit_yields_an_empty_queue(self, limit: int) -> None:
        plan = build_queue([card(1, days_overdue=5)], now=NOW, session_limit=limit)
        assert plan.is_empty

    def test_new_cards_can_be_switched_off(self) -> None:
        plan = build_queue([card(1, is_new=True)], now=NOW, new_card_limit=0)
        assert plan.is_empty
        assert plan.new_available == 1


class TestExclusions:
    def test_suspended_cards_never_appear(self) -> None:
        plan = build_queue(
            [card(1, days_overdue=100, suspended=True), card(2, is_new=True, suspended=True)],
            now=NOW,
        )
        assert plan.is_empty

    def test_cards_due_in_the_future_are_not_included(self) -> None:
        assert build_queue([card(1, days_overdue=-1)], now=NOW).is_empty

    def test_a_card_due_exactly_now_is_included(self) -> None:
        plan = build_queue([card(1, days_overdue=0)], now=NOW)
        assert reasons(plan) == [QueueReason.DUE]


class TestCounts:
    def test_counts_report_the_whole_backlog_not_just_the_session(self) -> None:
        plan = build_queue(
            [card(i, days_overdue=5) for i in range(30)]
            + [card(100 + i, days_overdue=0.2) for i in range(10)]
            + [card(200 + i, is_new=True) for i in range(50)],
            now=NOW,
            session_limit=10,
        )
        assert plan.overdue_count == 30
        assert plan.due_count == 10
        assert plan.new_available == 50
        assert plan.size == 10

    def test_counts_by_reason_covers_every_reason(self) -> None:
        plan = build_queue([card(1, days_overdue=5)], now=NOW)
        assert set(plan.counts_by_reason()) == set(QueueReason)


class TestDeterminism:
    def test_identical_input_yields_an_identical_queue(self) -> None:
        cards = [card(i, days_overdue=i % 3, concept_id=i % 4) for i in range(30)]
        runs = [build_queue(cards, now=NOW, weak_concept_ids=frozenset({1, 2})) for _ in range(5)]
        assert all(run.entries == runs[0].entries for run in runs)

    def test_ties_break_on_card_id(self) -> None:
        cards = [card(9), card(3), card(7)]
        plan = build_queue(cards, now=NOW)
        assert [e.card_id for e in plan.entries] == [3, 7, 9]

    def test_input_order_does_not_matter(self) -> None:
        cards = [card(i, days_overdue=i % 5) for i in range(20)]
        forward = build_queue(cards, now=NOW)
        backward = build_queue(list(reversed(cards)), now=NOW)
        assert forward.entries == backward.entries


class TestEmpty:
    def test_no_cards_yields_an_empty_plan(self) -> None:
        plan = build_queue([], now=NOW)
        assert plan.is_empty
        assert plan.size == 0
        assert plan.overdue_count == plan.due_count == plan.new_available == 0
