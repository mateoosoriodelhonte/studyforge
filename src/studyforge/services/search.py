"""Local full-text search over courses, documents, concepts and flashcards.

Built on **SQLite FTS5**, which ships inside SQLite itself. No extra process, no
service to run, no vector store. At the scale of one person's study material
that is not a compromise -- it is the right tool, and introducing embeddings or
a vector database here would be resume-driven architecture rather than
engineering.

The indexes are ordinary FTS5 tables kept in sync by database triggers (see the
``add full-text search`` migration). Triggers rather than application code
because the invariant is "the index matches the table", and that should hold
even for a row written by a migration, a script, or ``sqlite3`` by hand.

User input never reaches an FTS query unescaped. FTS5 has its own expression
syntax -- ``AND``, ``NEAR``, ``*``, quotes, parentheses -- and an unbalanced
quote is enough to turn a search box into a 500. See :func:`to_match_query`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

ResultKind = Literal["course", "document", "concept", "flashcard"]

#: Words stripped from a natural-language question before retrieval. Not a
#: general stoplist -- just the interrogatives and function words that appear
#: in questions but almost never in the notes being searched.
QUESTION_WORDS = frozenset(
    [
        "what",
        "why",
        "how",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "whose",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "do",
        "does",
        "did",
        "done",
        "can",
        "could",
        "should",
        "would",
        "will",
        "shall",
        "may",
        "might",
        "must",
        "a",
        "an",
        "the",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "from",
        "by",
        "with",
        "about",
        "into",
        "over",
        "under",
        "and",
        "or",
        "but",
        "if",
        "then",
        "than",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "as",
        "so",
        "such",
        "i",
        "me",
        "my",
        "we",
        "our",
        "you",
        "your",
        "they",
        "them",
        "their",
        "he",
        "she",
        "his",
        "her",
        "tell",
        "explain",
        "describe",
        "give",
        "show",
        "me",
        "please",
    ]
)

#: Per-kind cap, so one very common word cannot flood the page.
DEFAULT_LIMIT = 12

#: Characters that mean something to the FTS5 query parser.
_FTS_SPECIAL = re.compile(r'["\'()*:^{}\[\]\-+~]')
_TOKEN = re.compile(r"\w+", re.UNICODE)

KIND_LABELS: dict[ResultKind, str] = {
    "course": "Courses",
    "document": "Documents",
    "concept": "Concepts",
    "flashcard": "Flashcards",
}


@dataclass(frozen=True, slots=True)
class SearchResult:
    kind: ResultKind
    entity_id: int
    title: str
    snippet: str | None
    url: str
    context_label: str | None = None


@dataclass(frozen=True, slots=True)
class SearchGroup:
    kind: ResultKind
    label: str
    results: list[SearchResult]


def to_match_query(raw: str) -> str | None:
    """Turn user text into a safe FTS5 MATCH expression.

    Every FTS5 operator is stripped rather than escaped, and each remaining word
    is quoted as a literal. The result is an implicit-AND prefix search: typing
    ``binary tree`` finds rows containing both words, and a trailing ``*`` on the
    last token makes the search feel live as you type.

    Returns ``None`` when nothing searchable is left, which callers render as an
    empty state rather than running a query that matches everything.
    """
    if not raw or not raw.strip():
        return None
    cleaned = _FTS_SPECIAL.sub(" ", raw)
    tokens = _TOKEN.findall(cleaned)
    if not tokens:
        return None
    tokens = tokens[:8]  # a sentence-length query is a mistake, not a search
    quoted = [f'"{token}"' for token in tokens[:-1]]
    # Prefix-match only the final token, so results narrow as the user types
    # without "bin" matching nothing until they finish the word.
    quoted.append(f'"{tokens[-1]}"*')
    return " AND ".join(quoted)


def to_retrieval_query(raw: str) -> str | None:
    """Turn a natural-language *question* into an FTS5 expression.

    Different from :func:`to_match_query` on purpose. Search-as-you-type wants
    implicit AND: every word you type should narrow the results. A question does
    the opposite -- "Why are AVL tree operations logarithmic?" contains four
    words the notes will never contain, and ANDing them finds nothing.

    So: drop the interrogatives and function words, then OR what remains and let
    FTS5's ``rank`` order by relevance. A passage matching three content words
    outranks one matching a single word, which is the behaviour that makes
    retrieval useful.
    """
    if not raw or not raw.strip():
        return None
    cleaned = _FTS_SPECIAL.sub(" ", raw)
    tokens = [
        token
        for token in _TOKEN.findall(cleaned)
        if token.lower() not in QUESTION_WORDS and len(token) > 1
    ]
    if not tokens:
        # A question made entirely of function words has nothing to retrieve on.
        return None
    return " OR ".join(f'"{token}"' for token in tokens[:12])


def search(
    session: Session,
    raw_query: str,
    *,
    course_id: int | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[SearchGroup]:
    """Search everything, grouped by entity type and ranked within each group."""
    match = to_match_query(raw_query)
    if match is None:
        return []

    groups = [
        _search_courses(session, match, limit=limit) if course_id is None else None,
        _search_documents(session, match, course_id=course_id, limit=limit),
        _search_concepts(session, match, course_id=course_id, limit=limit),
        _search_flashcards(session, match, course_id=course_id, limit=limit),
    ]
    return [group for group in groups if group is not None and group.results]


def _search_courses(session: Session, match: str, *, limit: int) -> SearchGroup:
    rows = session.execute(
        text(
            """
            SELECT c.id, c.name, c.code, c.description
            FROM courses_fts f
            JOIN courses c ON c.id = f.rowid
            WHERE courses_fts MATCH :match
            ORDER BY rank
            LIMIT :limit
            """
        ),
        {"match": match, "limit": limit},
    ).all()
    return SearchGroup(
        kind="course",
        label=KIND_LABELS["course"],
        results=[
            SearchResult(
                kind="course",
                entity_id=row.id,
                title=row.name,
                snippet=row.description,
                url=f"/courses/{row.id}",
                context_label=row.code,
            )
            for row in rows
        ],
    )


def _search_documents(
    session: Session, match: str, *, course_id: int | None, limit: int
) -> SearchGroup:
    rows = session.execute(
        text(
            """
            SELECT d.id, d.title, d.course_id, c.name AS course_name,
                   snippet(documents_fts, 1, '', '', '…', 18) AS excerpt
            FROM documents_fts f
            JOIN documents d ON d.id = f.rowid
            JOIN courses c ON c.id = d.course_id
            WHERE documents_fts MATCH :match
              AND (:course_id IS NULL OR d.course_id = :course_id)
            ORDER BY rank
            LIMIT :limit
            """
        ),
        {"match": match, "course_id": course_id, "limit": limit},
    ).all()
    return SearchGroup(
        kind="document",
        label=KIND_LABELS["document"],
        results=[
            SearchResult(
                kind="document",
                entity_id=row.id,
                title=row.title,
                snippet=row.excerpt,
                url=f"/documents/{row.id}",
                context_label=row.course_name,
            )
            for row in rows
        ],
    )


def _search_concepts(
    session: Session, match: str, *, course_id: int | None, limit: int
) -> SearchGroup:
    rows = session.execute(
        text(
            """
            SELECT k.id, k.name, k.definition, k.course_id, c.name AS course_name
            FROM concepts_fts f
            JOIN concepts k ON k.id = f.rowid
            JOIN courses c ON c.id = k.course_id
            WHERE concepts_fts MATCH :match
              AND (:course_id IS NULL OR k.course_id = :course_id)
            ORDER BY rank
            LIMIT :limit
            """
        ),
        {"match": match, "course_id": course_id, "limit": limit},
    ).all()
    return SearchGroup(
        kind="concept",
        label=KIND_LABELS["concept"],
        results=[
            SearchResult(
                kind="concept",
                entity_id=row.id,
                title=row.name,
                snippet=row.definition,
                url=f"/courses/{row.course_id}",
                context_label=row.course_name,
            )
            for row in rows
        ],
    )


def _search_flashcards(
    session: Session, match: str, *, course_id: int | None, limit: int
) -> SearchGroup:
    rows = session.execute(
        text(
            """
            SELECT fc.id, fc.front, fc.back, fc.course_id, c.name AS course_name
            FROM flashcards_fts f
            JOIN flashcards fc ON fc.id = f.rowid
            JOIN courses c ON c.id = fc.course_id
            WHERE flashcards_fts MATCH :match
              AND (:course_id IS NULL OR fc.course_id = :course_id)
            ORDER BY rank
            LIMIT :limit
            """
        ),
        {"match": match, "course_id": course_id, "limit": limit},
    ).all()
    return SearchGroup(
        kind="flashcard",
        label=KIND_LABELS["flashcard"],
        results=[
            SearchResult(
                kind="flashcard",
                entity_id=row.id,
                title=row.front,
                snippet=row.back,
                url=f"/courses/{row.course_id}#cards",
                context_label=row.course_name,
            )
            for row in rows
        ],
    )


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """A passage retrieved to answer a question."""

    chunk_id: int
    text: str
    heading: str | None
    ordinal: int
    document_id: int
    document_title: str
    course_id: int


def retrieve_chunks(
    session: Session, raw_query: str, *, course_id: int | None = None, limit: int = 5
) -> list[RetrievedChunk]:
    """Retrieve document passages relevant to a question.

    The retrieval half of "ask my notes". Deliberately separate from any
    generation step: this is useful on its own with no AI configured, and when a
    provider *is* configured these passages are the only thing it is allowed to
    answer from.
    """
    match = to_retrieval_query(raw_query)
    if match is None:
        return []
    rows = session.execute(
        text(
            """
            SELECT ch.id, ch.text, ch.heading, ch.ordinal,
                   d.id AS document_id, d.title AS document_title, d.course_id
            FROM chunks_fts f
            JOIN document_chunks ch ON ch.id = f.rowid
            JOIN documents d ON d.id = ch.document_id
            WHERE chunks_fts MATCH :match
              AND (:course_id IS NULL OR d.course_id = :course_id)
            ORDER BY rank
            LIMIT :limit
            """
        ),
        {"match": match, "course_id": course_id, "limit": limit},
    ).all()
    return [
        RetrievedChunk(
            chunk_id=row.id,
            text=row.text,
            heading=row.heading,
            ordinal=row.ordinal,
            document_id=row.document_id,
            document_title=row.document_title,
            course_id=row.course_id,
        )
        for row in rows
    ]
