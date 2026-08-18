"""Sample data, for trying StudyForge out and for taking screenshots.

Everything here is **original material written for this project** and is
labelled as sample data in the UI. No textbook, lecture handout or other
copyrighted content is packaged with StudyForge.

The notes are deliberately written the way real notes are -- headings,
definitions, a glossary block, some prose -- because that is what exercises the
extraction pipeline honestly. Material tailored to make the extractor look good
would prove nothing.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from studyforge.domain.study.fsrs import Rating
from studyforge.logging_config import log_event
from studyforge.models import Course, Flashcard
from studyforge.services import documents as document_service
from studyforge.services import flashcards as card_service
from studyforge.services import quizzes as quiz_service
from studyforge.services import study as study_service

logger = logging.getLogger(__name__)

DEMO_COURSE_NAME = "Data Structures (sample)"

TREES_NOTES = """
Binary Search Trees

A binary search tree is a rooted binary tree in which every node stores a key
greater than all the keys in its left subtree and less than all the keys in its
right subtree. Lookup, insertion and deletion each follow a single path from the
root to a leaf, so their cost is proportional to the height of the tree.

On a balanced tree the height is logarithmic in the number of nodes, giving
O(log n) for all three operations. On a degenerate tree that has collapsed into
a linked list, the height is n and every operation degrades to O(n). Keeping the
height small is therefore the whole problem.

Self-Balancing Trees

An AVL tree is a self-balancing binary search tree that keeps the balance factor
of every node within the set negative one, zero and one. After an insertion or a
deletion the invariant is restored with at most one rotation or double rotation,
so updates remain logarithmic.

A red-black tree is a self-balancing binary search tree that colours each node
red or black and maintains the property that every path from a node to a leaf
contains the same number of black nodes. It permits slightly more imbalance than
an AVL tree, which makes updates cheaper and lookups marginally slower.

Glossary

Balance factor: the height of a node's right subtree minus the height of its
left subtree.

Rotation - a local restructuring that moves one node up and another down while
preserving the ordering of every key in the subtree.

Leaf - a node with no children, at the very bottom of the tree.
"""

COMPLEXITY_NOTES = """
Asymptotic Analysis

Big-O notation is an upper bound on how a function grows as its input grows,
ignoring constant factors and lower-order terms. Saying an algorithm is O(n log
n) claims that beyond some input size its cost never exceeds a constant multiple
of n log n.

Amortised analysis is a technique for averaging the cost of an operation across
a long sequence of operations rather than measuring the worst single case. A
dynamic array's append is O(n) whenever it must resize, but O(1) amortised,
because resizes become exponentially rarer as the array grows.

Sorting

Quicksort is a divide and conquer sorting algorithm that partitions the array
around a chosen pivot and then sorts each partition recursively. It averages
O(n log n) but degrades to O(n squared) when the pivot is consistently poor.

Mergesort is a divide and conquer sorting algorithm that splits the array in
half, sorts each half recursively, and merges the sorted halves. It is O(n log
n) in every case, at the cost of requiring additional memory.

Heapsort is a comparison sort that builds a binary heap from the array and then
repeatedly extracts the maximum element. It is O(n log n) in every case and
sorts in place, but its access pattern makes it slower than quicksort in
practice.
"""


def seed_demo_course(session: Session, *, now: datetime | None = None) -> Course:
    """Create the sample course, its material, cards, a quiz, and some history.

    The review history is generated so that the dashboard and progress pages
    have something real to display. It is written through the ordinary services,
    so the schedule and the weak-concept analysis are genuine outputs of the
    real engine rather than invented numbers.
    """
    now = now or datetime.now(UTC)

    course = Course(
        name=DEMO_COURSE_NAME,
        code="CS 2420",
        description=(
            "Sample data shipped with StudyForge so you can see it working. "
            "The notes are original, written for this project. Delete this "
            "course whenever you like."
        ),
    )
    session.add(course)
    session.flush()

    for title, body in (
        ("Trees and balancing", TREES_NOTES),
        ("Complexity and sorting", COMPLEXITY_NOTES),
    ):
        document_service.ingest_pasted_text(session, course_id=course.id, title=title, body=body)

    summary = card_service.generate_and_accept(session, course_id=course.id)
    quiz_service.generate_quiz(
        session, course_id=course.id, title="Trees and complexity", question_count=8
    )

    _simulate_history(session, cards=summary.created, now=now)

    session.flush()
    log_event(
        logger,
        "demo_course_seeded",
        course_id=course.id,
        cards=summary.created_count,
    )
    return course


def _simulate_history(session: Session, *, cards: list[Flashcard], now: datetime) -> None:
    """Review a few cards so the dashboard is not empty on first look.

    A fixed pattern rather than random: the sample data must look the same for
    everyone, and "demo data that differs every run" is impossible to screenshot
    or to write a test against.
    """
    if not cards:
        return

    study_session = study_service.start_session(
        session, course_id=None, now=now - timedelta(days=6)
    )
    pattern = [Rating.GOOD, Rating.GOOD, Rating.AGAIN, Rating.EASY, Rating.HARD, Rating.GOOD]

    for index, card in enumerate(cards[:8]):
        rating = pattern[index % len(pattern)]
        study_service.record_review(
            session,
            card_id=card.id,
            rating=rating,
            study_session_id=study_session.id,
            now=now - timedelta(days=6, minutes=-index),
        )
    study_service.end_session(session, study_session.id, now=now - timedelta(days=6))
