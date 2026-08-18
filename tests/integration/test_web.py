"""The server-rendered interface and the JSON API, driven end to end."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

NOTES = """
Binary Search Trees

A binary search tree is a rooted binary tree in which every node stores a key
greater than all keys in its left subtree.

An AVL tree is a self-balancing binary search tree that keeps every balance
factor within the set negative one, zero and one.

Balance factor: the height of a node's right subtree minus the height of its
left subtree.

Rotation - a local restructuring operation that restores the tree invariant
after an insertion or a deletion.

A heap is a complete binary tree satisfying the heap ordering property at every
one of its nodes.

Quicksort is a divide and conquer sorting algorithm that partitions an array
around a chosen pivot element.
"""


@pytest.fixture
def course_id(client: TestClient) -> int:
    response = client.post("/api/courses", json={"name": "Data Structures", "code": "CS 2420"})
    assert response.status_code == 201
    return int(response.json()["id"])


@pytest.fixture
def stocked(client: TestClient, course_id: int) -> int:
    client.post(
        f"/api/courses/{course_id}/documents/paste",
        json={"title": "Lecture 1", "body": NOTES},
    )
    client.post(f"/api/courses/{course_id}/flashcards/generate")
    return course_id


class TestPagesRender:
    @pytest.mark.parametrize(
        "path", ["/", "/dashboard", "/progress", "/settings", "/search", "/study"]
    )
    def test_core_pages_return_html(self, client: TestClient, path: str) -> None:
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert '<html lang="en">' in response.text

    def test_every_page_has_a_skip_link_and_live_region(self, client: TestClient) -> None:
        """Two things the whole keyboard and screen-reader experience rests on."""
        body = client.get("/dashboard").text
        assert 'class="skip-link"' in body
        assert 'id="live-region"' in body
        assert 'aria-live="polite"' in body

    def test_navigation_marks_the_current_page(self, client: TestClient) -> None:
        assert 'aria-current="page"' in client.get("/dashboard").text

    def test_a_missing_page_renders_a_friendly_404(self, client: TestClient) -> None:
        response = client.get("/no-such-page")
        assert response.status_code == 404
        assert "Traceback" not in response.text

    def test_a_missing_course_renders_the_service_message(self, client: TestClient) -> None:
        response = client.get("/courses/99999")
        assert response.status_code == 404
        assert "does not exist" in response.text
        assert "Traceback" not in response.text

    def test_static_assets_are_served(self, client: TestClient) -> None:
        for asset in ("/static/app.css", "/static/app.js", "/static/htmx.min.js"):
            assert client.get(asset).status_code == 200

    def test_htmx_is_vendored_not_fetched_from_a_cdn(self, client: TestClient) -> None:
        """A local-first app should render with no network access at all."""
        body = client.get("/dashboard").text
        for cdn in ("unpkg.com", "cdn.jsdelivr", "cdnjs.cloudflare", "googleapis.com"):
            assert cdn not in body
        # Every script and stylesheet must resolve to this application's own
        # /static mount, never to a third-party host.
        for url in re.findall(r'(?:src|href)="([^"]+)"', body):
            if url.startswith(("http://", "https://")):
                assert "/static/" in url or "github.com" in url, f"external asset: {url}"


class TestCourseLifecycle:
    def test_create_via_the_form_and_land_on_the_course(self, client: TestClient) -> None:
        response = client.post(
            "/courses/new", data={"name": "Algorithms", "code": "CS 3510", "description": ""}
        )
        assert response.status_code == 200  # redirect followed
        assert "Algorithms" in response.text

    def test_a_blank_name_re_renders_the_form_with_a_field_error(self, client: TestClient) -> None:
        response = client.post("/courses/new", data={"name": "   "})
        assert response.status_code == 422
        assert 'aria-invalid="true"' in response.text
        assert 'id="error-name"' in response.text
        assert 'aria-describedby="error-name"' in response.text

    def test_the_course_page_shows_its_material(self, client: TestClient, stocked: int) -> None:
        body = client.get(f"/courses/{stocked}").text
        assert "Documents" in body
        assert "Concepts" in body
        assert "AVL tree" in body

    def test_an_empty_course_shows_a_real_empty_state(
        self, client: TestClient, course_id: int
    ) -> None:
        body = client.get(f"/courses/{course_id}").text
        assert "nothing in it yet" in body
        assert "Add study material" in body

    def test_archiving_hides_it_from_the_dashboard_without_deleting(
        self, client: TestClient, course_id: int
    ) -> None:
        client.post(f"/courses/{course_id}/archive")
        assert "Data Structures" not in client.get("/dashboard").text
        assert client.get(f"/courses/{course_id}").status_code == 200


class TestDocumentFlow:
    def test_pasting_notes_produces_chunks_and_concepts(
        self, client: TestClient, course_id: int
    ) -> None:
        response = client.post(
            f"/courses/{course_id}/documents/paste",
            data={"title": "Lecture 1", "body": NOTES},
        )
        assert response.status_code == 200
        assert "Chunks" in response.text
        assert "Concepts found here" in response.text

    def test_uploading_a_text_file_works(self, client: TestClient, course_id: int) -> None:
        response = client.post(
            f"/courses/{course_id}/documents/upload",
            files={"file": ("notes.txt", NOTES.encode(), "text/plain")},
            data={"title": ""},
        )
        assert response.status_code == 200
        assert "notes.txt" in response.text

    def test_a_rejected_upload_re_renders_the_form_with_the_reason(
        self, client: TestClient, course_id: int
    ) -> None:
        response = client.post(
            f"/courses/{course_id}/documents/upload",
            files={"file": ("evil.exe", b"MZ\x90\x00", "application/octet-stream")},
        )
        assert response.status_code == 400
        assert "cannot read" in response.text
        assert "Traceback" not in response.text

    def test_an_oversized_upload_is_refused(
        self, client: TestClient, course_id: int, settings: object
    ) -> None:
        oversized = b"x" * (settings.max_upload_bytes + 1024)  # type: ignore[attr-defined]
        response = client.post(
            f"/courses/{course_id}/documents/upload",
            files={"file": ("big.txt", oversized, "text/plain")},
        )
        assert response.status_code == 400
        assert "limit" in response.text


class TestStudyFlow:
    def test_the_review_loop_reveals_then_rates(self, client: TestClient, stocked: int) -> None:
        page = client.get(f"/study?course_id={stocked}")
        assert page.status_code == 200
        assert "Show answer" in page.text

        session_id = int(re.search(r"session_id=(\d+)", page.text).group(1))  # type: ignore[union-attr]
        card_id = int(re.search(r"card_id=(\d+)", page.text).group(1))  # type: ignore[union-attr]

        revealed = client.get(
            f"/study/reveal?card_id={card_id}&session_id={session_id}&position=1&total=4&reason=new"
        )
        assert revealed.status_code == 200
        assert 'data-rating="3"' in revealed.text
        assert "Again" in revealed.text and "Easy" in revealed.text

        rated = client.post(
            "/study/review",
            data={"card_id": card_id, "rating": 3, "session_id": session_id},
        )
        assert rated.status_code == 200

    def test_rating_buttons_show_the_real_intervals(self, client: TestClient, stocked: int) -> None:
        """The learner should see the schedule, not be asked to trust it."""
        page = client.get(f"/study?course_id={stocked}")
        session_id = int(re.search(r"session_id=(\d+)", page.text).group(1))  # type: ignore[union-attr]
        card_id = int(re.search(r"card_id=(\d+)", page.text).group(1))  # type: ignore[union-attr]
        revealed = client.get(
            f"/study/reveal?card_id={card_id}&session_id={session_id}&position=1&total=1&reason=new"
        )
        assert 'class="rating__interval"' in revealed.text
        assert re.search(r'rating__interval">\s*\d+[mhdy]', revealed.text)

    def test_an_empty_queue_says_so_rather_than_breaking(
        self, client: TestClient, course_id: int
    ) -> None:
        response = client.get(f"/study?course_id={course_id}")
        assert response.status_code == 200
        assert "Nothing due right now" in response.text

    def test_keyboard_hints_are_present(self, client: TestClient, stocked: int) -> None:
        body = client.get(f"/study?course_id={stocked}").text
        assert "<kbd>Space</kbd>" in body
        assert "data-study-session" in body


class TestQuizFlow:
    def test_generate_take_and_finish(self, client: TestClient, stocked: int) -> None:
        generated = client.post(f"/courses/{stocked}/quizzes/generate")
        assert generated.status_code == 200
        assert "Question 1 of" in generated.text

        attempt_id = int(re.search(r"/quizzes/attempts/(\d+)/", generated.text).group(1))  # type: ignore[union-attr]
        question_id = int(
            re.search(r'name="question_id" value="(\d+)"', generated.text).group(1)  # type: ignore[union-attr]
        )

        answered = client.post(
            f"/quizzes/attempts/{attempt_id}/answer",
            data={"question_id": question_id, "response": "clearly wrong"},
        )
        assert answered.status_code == 200
        assert "Not quite" in answered.text

        finished = client.post(f"/quizzes/attempts/{attempt_id}/complete")
        assert finished.status_code == 200
        assert "Quiz finished" in finished.text

    def test_a_multiple_choice_grade_cannot_be_overridden(
        self, client: TestClient, stocked: int
    ) -> None:
        """Multiple choice is graded objectively, so an override would be nonsense."""
        page = client.post(f"/courses/{stocked}/quizzes/generate")
        attempt_id = int(re.search(r"/quizzes/attempts/(\d+)/", page.text).group(1))  # type: ignore[union-attr]
        assert 'type="radio"' in page.text, "this fixture should yield multiple choice"

        question_id = int(re.search(r'name="question_id" value="(\d+)"', page.text).group(1))  # type: ignore[union-attr]
        answered = client.post(
            f"/quizzes/attempts/{attempt_id}/answer",
            data={"question_id": question_id, "response": "999"},
        )
        assert "Not quite" in answered.text
        assert "I was actually right" not in answered.text

    def test_a_short_answer_grade_can_be_overridden(
        self, client: TestClient, course_id: int
    ) -> None:
        """Short answers are graded by text comparison, which can be unfair to a
        correct paraphrase — so the learner gets an explicit override.

        A two-concept course cannot supply enough sibling definitions for
        plausible distractors, so generation falls back to short answer. That
        fallback is exactly what makes this test possible.
        """
        client.post(
            f"/api/courses/{course_id}/documents/paste",
            json={
                "title": "Small",
                "body": (
                    "An AVL tree is a self-balancing binary search tree that keeps "
                    "every balance factor within negative one, zero and one.\n\n"
                    "A heap is a complete binary tree satisfying the heap ordering "
                    "property at every one of its nodes."
                ),
            },
        )
        page = client.post(f"/courses/{course_id}/quizzes/generate")
        assert 'type="radio"' not in page.text, "too few concepts for fair multiple choice"

        attempt_id = int(re.search(r"/quizzes/attempts/(\d+)/", page.text).group(1))  # type: ignore[union-attr]
        question_id = int(re.search(r'name="question_id" value="(\d+)"', page.text).group(1))  # type: ignore[union-attr]

        answered = client.post(
            f"/quizzes/attempts/{attempt_id}/answer",
            data={"question_id": question_id, "response": "my own wording of it"},
        )
        assert "Not quite" in answered.text
        assert "I was actually right" in answered.text

        overridden = client.post(
            f"/quizzes/attempts/{attempt_id}/self-grade",
            data={"question_id": question_id},
        )
        assert "Correct" in overridden.text

        finished = client.post(f"/quizzes/attempts/{attempt_id}/complete")
        assert "Self-graded" in finished.text, "self-marking must be reported separately"

    def test_a_course_with_no_material_explains_why_there_is_no_quiz(
        self, client: TestClient, course_id: int
    ) -> None:
        response = client.post(f"/courses/{course_id}/quizzes/generate")
        assert response.status_code == 200
        assert "no concepts with definitions" in response.text.lower()


class TestSearch:
    def test_finds_across_entity_types(self, client: TestClient, stocked: int) -> None:
        body = client.get("/search/results?q=binary").text
        assert "Concepts" in body or "Documents" in body
        assert "<mark>" in body, "matches should be highlighted"

    def test_no_matches_renders_an_empty_state(self, client: TestClient, stocked: int) -> None:
        body = client.get("/search/results?q=zzzznotathing").text
        assert "No matches" in body

    def test_an_empty_query_prompts_rather_than_listing_everything(
        self, client: TestClient
    ) -> None:
        assert "Type to search" in client.get("/search/results?q=").text

    def test_the_index_follows_deletions(self, client: TestClient, stocked: int) -> None:
        """A stale index would keep offering links to things that are gone."""
        assert "<mark>" in client.get("/search/results?q=quicksort").text
        card_ids = re.findall(r'hx-delete="/cards/(\d+)"', client.get(f"/courses/{stocked}").text)
        for card_id in card_ids:
            client.delete(f"/cards/{card_id}")
        body = client.get("/search/results?q=quicksort").text
        assert "Flashcards" not in body


class TestJsonApi:
    def test_openapi_documents_the_surface(self, client: TestClient) -> None:
        schema = client.get("/api/openapi.json").json()
        paths = set(schema["paths"])
        assert "/api/courses" in paths
        assert "/api/reviews" in paths
        assert "/api/progress" in paths
        for path in paths:
            assert path.startswith("/api/"), "the API must not leak HTML routes into OpenAPI"

    def test_full_json_round_trip(self, client: TestClient) -> None:
        created = client.post("/api/courses", json={"name": "Networks"}).json()
        assert client.get(f"/api/courses/{created['id']}").json()["stats"]["documents"] == 0
        updated = client.put(
            f"/api/courses/{created['id']}", json={"name": "Computer Networks"}
        ).json()
        assert updated["name"] == "Computer Networks"

    def test_validation_errors_are_422_with_detail(self, client: TestClient) -> None:
        response = client.post("/api/courses", json={"name": ""})
        assert response.status_code == 422
        assert "detail" in response.json()

    def test_a_missing_resource_is_json_not_html(self, client: TestClient) -> None:
        response = client.get("/api/courses/99999")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
        assert "does not exist" in response.json()["detail"]

    def test_reviews_reject_an_out_of_range_rating(self, client: TestClient, stocked: int) -> None:
        card_id = client.get(f"/api/courses/{stocked}/flashcards").json()[0]["id"]
        for rating in (0, 5, -1):
            response = client.post("/api/reviews", json={"card_id": card_id, "rating": rating})
            assert response.status_code == 422

    def test_a_review_returns_the_new_schedule(self, client: TestClient, stocked: int) -> None:
        card_id = client.get(f"/api/courses/{stocked}/flashcards").json()[0]["id"]
        body = client.post("/api/reviews", json={"card_id": card_id, "rating": 3}).json()
        assert body["interval_days"] >= 0
        assert body["stability"] is not None
        assert body["was_duplicate"] is False

    def test_progress_reports_null_rates_when_there_is_no_data(self, client: TestClient) -> None:
        """Clients must be able to tell "no data" from "scored zero"."""
        body = client.get("/api/progress").json()
        assert body["review_recall"]["value"] is None
        assert body["review_recall"]["denominator"] == 0

    def test_the_queue_is_ordered_and_explains_itself(
        self, client: TestClient, stocked: int
    ) -> None:
        body = client.get("/api/reviews/queue").json()
        assert body["entries"]
        assert [e["position"] for e in body["entries"]] == list(range(len(body["entries"])))
        assert all(
            e["reason"] in {"overdue", "due", "weak_concept", "new"} for e in body["entries"]
        )


class TestFlashCards:
    def test_suspend_and_restore_swap_the_row(self, client: TestClient, stocked: int) -> None:
        card_id = client.get(f"/api/courses/{stocked}/flashcards").json()[0]["id"]
        suspended = client.post(f"/cards/{card_id}/suspend")
        assert suspended.status_code == 200
        assert "Suspended" in suspended.text
        restored = client.post(f"/cards/{card_id}/unsuspend")
        assert "Suspended" not in restored.text

    def test_deleting_returns_an_empty_body_to_remove_the_row(
        self, client: TestClient, stocked: int
    ) -> None:
        card_id = client.get(f"/api/courses/{stocked}/flashcards").json()[0]["id"]
        response = client.delete(f"/cards/{card_id}")
        assert response.status_code == 200
        assert response.text == ""

    def test_generated_cards_link_back_to_their_source(
        self, client: TestClient, stocked: int
    ) -> None:
        body = client.get(f"/courses/{stocked}").text
        assert re.search(r'href="/documents/\d+">source</a>', body)


class TestSettingsPage:
    def test_reports_no_ai_honestly(self, client: TestClient) -> None:
        body = client.get("/settings").text
        assert "Everything stays local" in body
        assert "fully functional this way" in body

    def test_shows_where_data_lives(self, client: TestClient, db_session: Session) -> None:
        body = client.get("/settings").text
        assert "Database" in body
        assert "Uploads" in body

    def test_states_that_no_model_touches_scheduling(self, client: TestClient) -> None:
        body = client.get("/settings").text
        assert "No language model influences scheduling" in body
        assert "FSRS-6" in body


class TestNoFakeContent:
    """The README and UI must not invent users, universities or metrics."""

    @pytest.mark.parametrize("path", ["/", "/dashboard", "/progress", "/settings"])
    def test_no_invented_social_proof(self, client: TestClient, path: str) -> None:
        body = client.get(path).text.lower()
        for phrase in (
            "trusted by",
            "students love",
            "join thousands",
            "testimonial",
            "5-star",
            "rated #1",
        ):
            assert phrase not in body

    def test_a_fresh_install_shows_empty_states_not_zeroes_as_insight(
        self, client: TestClient
    ) -> None:
        body = client.get("/progress").text
        assert "Nothing to report yet" in body
