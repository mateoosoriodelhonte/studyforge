"""SQLAlchemy models.

Importing this package registers every mapper on :class:`Base`, which is what
Alembic's autogenerate and ``Base.metadata.create_all`` both rely on. Import the
package, not individual modules, when you need the full metadata.
"""

from studyforge.models.base import Base, TimestampMixin, UTCDateTime, utcnow
from studyforge.models.concept import Concept
from studyforge.models.course import Course
from studyforge.models.document import Document, DocumentChunk
from studyforge.models.enums import (
    DocumentSource,
    DocumentStatus,
    ExtractionMethod,
    GenerationMethod,
    QuestionKind,
)
from studyforge.models.flashcard import Flashcard, Review
from studyforge.models.quiz import AnswerAttempt, Question, Quiz, QuizAttempt
from studyforge.models.session import StudySession

__all__ = [
    "AnswerAttempt",
    "Base",
    "Concept",
    "Course",
    "Document",
    "DocumentChunk",
    "DocumentSource",
    "DocumentStatus",
    "ExtractionMethod",
    "Flashcard",
    "GenerationMethod",
    "Question",
    "QuestionKind",
    "Quiz",
    "QuizAttempt",
    "Review",
    "StudySession",
    "TimestampMixin",
    "UTCDateTime",
    "utcnow",
]
