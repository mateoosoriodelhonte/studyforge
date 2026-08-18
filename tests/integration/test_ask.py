"""Ask My Notes: retrieval, grounding, and the no-AI path."""

from __future__ import annotations

import json

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from studyforge.ai.none import NoAIProvider
from studyforge.ai.ollama import OllamaProvider
from studyforge.models import Course
from studyforge.services import ask as ask_service
from studyforge.services import documents as document_service
from studyforge.services import search as search_service

NOTES = """
Balanced Trees

An AVL tree is a self-balancing binary search tree that keeps the balance factor
of every node within the set negative one, zero and one. Because the height stays
logarithmic, lookup and insertion remain logarithmic too.

Rotation - a local restructuring that moves one node up and another down while
preserving the ordering of every key in the subtree.
"""


@pytest.fixture
def course(db_session: Session) -> Course:
    db_session.add(course := Course(name="Data Structures"))
    db_session.flush()
    document_service.ingest_pasted_text(db_session, course_id=course.id, title="Trees", body=NOTES)
    db_session.commit()
    return course


def ollama_returning(payload: dict[str, object]) -> OllamaProvider:
    """An Ollama provider whose model returns exactly ``payload``."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "test-model"}]})
        return httpx.Response(200, json={"message": {"content": json.dumps(payload)}})

    return OllamaProvider(model="test-model", transport=httpx.MockTransport(handler))


class TestRetrievalQuery:
    def test_question_words_are_dropped(self) -> None:
        """ "Why are AVL trees balanced?" must not AND on "why" and "are"."""
        query = search_service.to_retrieval_query("Why are AVL tree operations logarithmic?")
        assert query is not None
        assert "why" not in query.lower()
        assert "are" not in query.lower()
        assert "avl" in query.lower()

    def test_terms_are_combined_with_or_so_partial_matches_rank(self) -> None:
        query = search_service.to_retrieval_query("AVL rotation balance")
        assert query == '"AVL" OR "rotation" OR "balance"'

    @pytest.mark.parametrize("question", ["", "   ", "what is the of and", "?!"])
    def test_a_question_with_no_content_words_retrieves_nothing(self, question: str) -> None:
        assert search_service.to_retrieval_query(question) is None

    def test_it_differs_from_the_search_query(self) -> None:
        """Search wants AND as you type; retrieval wants OR with ranking."""
        assert search_service.to_match_query("avl tree") != search_service.to_retrieval_query(
            "avl tree"
        )


class TestWithoutAI:
    async def test_retrieval_alone_is_a_real_answer(
        self, db_session: Session, course: Course
    ) -> None:
        answer = await ask_service.ask(
            db_session, NoAIProvider(), question="Why are AVL trees logarithmic?"
        )
        assert answer.has_evidence
        assert not answer.has_explanation
        assert answer.unavailable_reason
        assert any("AVL" in p.text for p in answer.passages)

    async def test_passages_can_be_cited_back_to_their_document(
        self, db_session: Session, course: Course
    ) -> None:
        answer = await ask_service.ask(db_session, NoAIProvider(), question="AVL rotation")
        passage = answer.passages[0]
        assert passage.document_title == "Trees"
        assert passage.citation
        assert passage.chunk_id > 0

    async def test_no_match_yields_no_evidence_and_no_answer(
        self, db_session: Session, course: Course
    ) -> None:
        answer = await ask_service.ask(
            db_session, NoAIProvider(), question="quantum chromodynamics"
        )
        assert not answer.has_evidence
        assert not answer.has_explanation

    @pytest.mark.parametrize("question", ["", "   "])
    async def test_an_empty_question_is_not_a_query(
        self, db_session: Session, course: Course, question: str
    ) -> None:
        answer = await ask_service.ask(db_session, NoAIProvider(), question=question)
        assert answer.question == ""
        assert not answer.has_evidence


class TestWithAI:
    async def test_a_grounded_answer_keeps_its_citations(
        self, db_session: Session, course: Course
    ) -> None:
        provider = ollama_returning(
            {"explanation": "Because the height stays logarithmic.", "used_sources": [0]}
        )
        answer = await ask_service.ask(
            db_session, provider, question="Why are AVL trees logarithmic?"
        )
        assert answer.is_grounded
        assert answer.cited
        assert answer.provider == "ollama"

    async def test_an_answer_citing_nothing_is_not_treated_as_grounded(
        self, db_session: Session, course: Course
    ) -> None:
        """An explanation that cites no source is not grounded in the notes."""
        provider = ollama_returning({"explanation": "Trust me.", "used_sources": []})
        answer = await ask_service.ask(db_session, provider, question="AVL trees")
        assert answer.has_explanation
        assert not answer.is_grounded

    async def test_citations_to_sources_that_were_not_supplied_are_dropped(
        self, db_session: Session, course: Course
    ) -> None:
        provider = ollama_returning({"explanation": "See source 40.", "used_sources": [40, 0, -3]})
        answer = await ask_service.ask(db_session, provider, question="AVL trees")
        assert len(answer.cited) == 1

    async def test_a_provider_failure_still_returns_the_passages(
        self, db_session: Session, course: Course
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [{"name": "test-model"}]})
            raise httpx.ConnectError("gone", request=request)

        provider = OllamaProvider(model="test-model", transport=httpx.MockTransport(handler))
        answer = await ask_service.ask(db_session, provider, question="AVL trees")
        assert answer.has_evidence, "retrieval must survive a generation failure"
        assert not answer.has_explanation
        assert answer.unavailable_reason

    async def test_only_the_retrieved_passages_are_sent(
        self, db_session: Session, course: Course
    ) -> None:
        """The prompt must never carry other documents, settings or secrets."""
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [{"name": "test-model"}]})
            captured["body"] = request.content.decode()
            return httpx.Response(
                200,
                json={
                    "message": {"content": json.dumps({"explanation": "ok", "used_sources": [0]})}
                },
            )

        document_service.ingest_pasted_text(
            db_session,
            course_id=course.id,
            title="Unrelated",
            body=(
                "Dijkstra's algorithm computes single source shortest paths in a "
                "weighted graph with non negative edge weights."
            ),
        )
        db_session.commit()

        provider = OllamaProvider(model="test-model", transport=httpx.MockTransport(handler))
        answer = await ask_service.ask(db_session, provider, question="AVL balance factor")

        assert "AVL" in captured["body"]
        assert "Dijkstra" not in captured["body"], "unrelated notes must not be sent"
        assert "sqlite" not in captured["body"].lower()
        assert answer.passages


class TestAskThroughTheWeb:
    def test_the_page_renders(self, client: TestClient) -> None:
        response = client.get("/ask")
        assert response.status_code == 200
        assert "Ask my notes" in response.text

    def test_asking_shows_the_passages_and_says_no_ai_is_configured(
        self, client: TestClient
    ) -> None:
        course = client.post("/api/courses", json={"name": "DS"}).json()
        client.post(
            f"/api/courses/{course['id']}/documents/paste",
            json={"title": "Trees", "body": NOTES},
        )
        response = client.post("/ask", data={"question": "Why are AVL trees logarithmic?"})
        assert response.status_code == 200
        assert "From your notes" in response.text
        assert "Showing your notes only" in response.text
        assert "Explanation" not in response.text

    def test_no_match_renders_an_honest_empty_state(self, client: TestClient) -> None:
        response = client.post("/ask", data={"question": "quantum chromodynamics"})
        assert "Nothing in your notes covers that" in response.text
        assert "only answers from what you have given it" in response.text
