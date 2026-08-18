"""Deterministic generation of study material from extracted concepts.

No AI. Given the same concepts, these functions always produce the same cards
and the same questions, in the same order.

The governing rule is **fewer, defensible items over more, weak ones**. Every
generator here returns nothing at all rather than padding its output with
material a learner would not trust. A wrong flashcard is worse than a missing
one: it teaches something false and then schedules it for repetition.
"""

from studyforge.domain.generation.flashcards import (
    CardCandidate,
    CardStrategy,
    generate_cards,
)
from studyforge.domain.generation.quizzes import (
    QuestionCandidate,
    generate_questions,
)

__all__ = [
    "CardCandidate",
    "CardStrategy",
    "QuestionCandidate",
    "generate_cards",
    "generate_questions",
]
