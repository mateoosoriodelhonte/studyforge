"""Weak-concept analysis: evidence thresholds, recency decay, honest labels."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from studyforge.domain.study.weakness import (
    MIN_OBSERVATIONS,
    NEEDS_WORK_THRESHOLD,
    STATUS_DEFINITIONS,
    STRONG_THRESHOLD,
    ConceptStatus,
    Observation,
    ObservationKind,
    assess_concepts,
    observation_window,
    weakest_concepts,
)

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def obs(
    *, correct: bool, days_ago: float = 0, concept_id: int = 1, count: int = 1
) -> list[Observation]:
    return [
        Observation(
            concept_id=concept_id,
            correct=correct,
            occurred_at=NOW - timedelta(days=days_ago, seconds=i),
        )
        for i in range(count)
    ]


class TestEvidenceThreshold:
    """Declaring mastery on a tiny sample is the commonest way study software lies."""

    @pytest.mark.parametrize("count", range(1, MIN_OBSERVATIONS))
    def test_too_few_observations_yields_no_judgement(self, count: int) -> None:
        assessment = assess_concepts(obs(correct=True, count=count), now=NOW)[1]
        assert assessment.status is ConceptStatus.NOT_ENOUGH_DATA
        assert assessment.accuracy is None, "accuracy must be None, never 0.0"
        assert not assessment.has_enough_data

    def test_perfect_but_tiny_history_is_not_strong(self) -> None:
        assessment = assess_concepts(obs(correct=True, count=MIN_OBSERVATIONS - 1), now=NOW)[1]
        assert assessment.status is not ConceptStatus.STRONG

    def test_awful_but_tiny_history_is_not_weak(self) -> None:
        assessment = assess_concepts(obs(correct=False, count=MIN_OBSERVATIONS - 1), now=NOW)[1]
        assert assessment.status is not ConceptStatus.NEEDS_WORK
        assert not assessment.needs_work

    def test_reaching_the_threshold_enables_judgement(self) -> None:
        assessment = assess_concepts(obs(correct=True, count=MIN_OBSERVATIONS), now=NOW)[1]
        assert assessment.status is ConceptStatus.STRONG
        assert assessment.accuracy == pytest.approx(1.0)


class TestClassification:
    def test_all_correct_is_strong(self) -> None:
        assert (
            assess_concepts(obs(correct=True, count=10), now=NOW)[1].status is ConceptStatus.STRONG
        )

    def test_all_incorrect_needs_work(self) -> None:
        assessment = assess_concepts(obs(correct=False, count=10), now=NOW)[1]
        assert assessment.status is ConceptStatus.NEEDS_WORK
        assert assessment.accuracy == pytest.approx(0.0)

    def test_a_middling_record_is_developing(self) -> None:
        observations = obs(correct=True, count=7) + obs(correct=False, count=3)
        assessment = assess_concepts(observations, now=NOW)[1]
        assert assessment.status is ConceptStatus.DEVELOPING
        assert NEEDS_WORK_THRESHOLD <= (assessment.accuracy or 0) < STRONG_THRESHOLD

    def test_the_incorrect_count_is_reported_alongside(self) -> None:
        observations = obs(correct=True, count=6) + obs(correct=False, count=4)
        assessment = assess_concepts(observations, now=NOW)[1]
        assert assessment.observation_count == 10
        assert assessment.incorrect_count == 4


class TestRecency:
    def test_recent_success_outweighs_old_failure(self) -> None:
        """Wrong in January, right all last week: that is not a weak concept."""
        observations = obs(correct=False, days_ago=120, count=6) + obs(
            correct=True, days_ago=1, count=6
        )
        assert assess_concepts(observations, now=NOW)[1].status is ConceptStatus.STRONG

    def test_recent_failure_outweighs_old_success(self) -> None:
        observations = obs(correct=True, days_ago=120, count=6) + obs(
            correct=False, days_ago=1, count=6
        )
        assert assess_concepts(observations, now=NOW)[1].status is ConceptStatus.NEEDS_WORK

    def test_ancient_observations_are_dropped_entirely(self) -> None:
        recent = obs(correct=True, days_ago=1, count=MIN_OBSERVATIONS)
        ancient = obs(correct=False, days_ago=400, count=50)
        assessment = assess_concepts(recent + ancient, now=NOW)[1]
        assert assessment.observation_count == MIN_OBSERVATIONS

    def test_a_concept_with_only_ancient_history_disappears(self) -> None:
        assert assess_concepts(obs(correct=True, days_ago=400, count=20), now=NOW) == {}

    def test_a_future_timestamp_cannot_dominate(self) -> None:
        """Clock skew must not let one observation outweigh everything else."""
        observations = obs(correct=True, days_ago=-30, count=1) + obs(correct=False, count=9)
        assessment = assess_concepts(observations, now=NOW)[1]
        assert assessment.status is ConceptStatus.NEEDS_WORK

    def test_the_window_helper_matches_the_drop_rule(self) -> None:
        assert observation_window(now=NOW) < NOW - timedelta(days=179)


class TestMultipleConcepts:
    def test_concepts_are_assessed_independently(self) -> None:
        observations = (
            obs(correct=True, count=8, concept_id=1)
            + obs(correct=False, count=8, concept_id=2)
            + obs(correct=True, count=2, concept_id=3)
        )
        assessments = assess_concepts(observations, now=NOW)
        assert assessments[1].status is ConceptStatus.STRONG
        assert assessments[2].status is ConceptStatus.NEEDS_WORK
        assert assessments[3].status is ConceptStatus.NOT_ENOUGH_DATA

    def test_both_observation_kinds_count(self) -> None:
        """A wrong quiz answer and an "Again" on a card say the same thing."""
        observations = [
            Observation(1, correct=False, occurred_at=NOW, kind=ObservationKind.QUIZ_ANSWER),
            Observation(1, correct=False, occurred_at=NOW, kind=ObservationKind.CARD_REVIEW),
            Observation(1, correct=False, occurred_at=NOW, kind=ObservationKind.CARD_REVIEW),
            Observation(1, correct=False, occurred_at=NOW, kind=ObservationKind.QUIZ_ANSWER),
        ]
        assert assess_concepts(observations, now=NOW)[1].status is ConceptStatus.NEEDS_WORK


class TestWeakestConcepts:
    def test_returns_worst_first(self) -> None:
        observations = (
            obs(correct=False, count=10, concept_id=1)
            + obs(correct=True, count=6, concept_id=2)
            + obs(correct=False, count=4, concept_id=2)
            + obs(correct=True, count=10, concept_id=3)
        )
        ranked = weakest_concepts(assess_concepts(observations, now=NOW))
        assert [a.concept_id for a in ranked] == [1, 2]

    def test_excludes_concepts_without_enough_evidence(self) -> None:
        """A "needs work" list must never include something barely seen."""
        observations = obs(correct=False, count=2, concept_id=9)
        assert weakest_concepts(assess_concepts(observations, now=NOW)) == []

    def test_excludes_strong_concepts(self) -> None:
        observations = obs(correct=True, count=10, concept_id=1)
        assert weakest_concepts(assess_concepts(observations, now=NOW)) == []

    def test_respects_the_limit(self) -> None:
        observations = [
            o for cid in range(1, 21) for o in obs(correct=False, count=5, concept_id=cid)
        ]
        assert len(weakest_concepts(assess_concepts(observations, now=NOW), limit=5)) == 5

    def test_is_empty_with_no_observations(self) -> None:
        assert weakest_concepts(assess_concepts([], now=NOW)) == []


class TestHonesty:
    def test_every_status_has_a_written_definition(self) -> None:
        """A label nobody can define is decoration."""
        assert set(STATUS_DEFINITIONS) == set(ConceptStatus)
        for definition in STATUS_DEFINITIONS.values():
            assert len(definition) > 30

    def test_an_assessment_can_explain_its_own_label(self) -> None:
        assessment = assess_concepts(obs(correct=True, count=10), now=NOW)[1]
        assert assessment.status_definition == STATUS_DEFINITIONS[ConceptStatus.STRONG]

    def test_assessment_is_deterministic(self) -> None:
        observations = obs(correct=True, count=5) + obs(correct=False, count=3)
        runs = [assess_concepts(observations, now=NOW) for _ in range(5)]
        assert all(run == runs[0] for run in runs)
