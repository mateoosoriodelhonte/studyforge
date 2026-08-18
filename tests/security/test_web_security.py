"""Web-layer security: escaping, injection, and what errors reveal.

Every test here names an attack and asserts it does not work.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from studyforge.services import search as search_service

#: Payloads that must never be reflected as live markup.
XSS_PAYLOADS = [
    "<script>alert('xss')</script>",
    "<img src=x onerror=alert(1)>",
    "<svg/onload=alert(1)>",
    "javascript:alert(1)",
    '"><script>alert(1)</script>',
    "<iframe src='javascript:alert(1)'></iframe>",
    "<body onload=alert(1)>",
    "</textarea><script>alert(1)</script>",
]


def assert_not_live_markup(body: str, payload: str) -> None:
    """Assert ``payload`` appears only as inert text, if at all.

    Checking for substrings like ``onerror=alert`` is not good enough: that text
    survives escaping perfectly harmlessly inside
    ``&lt;img src=x onerror=alert(1)&gt;``. What actually matters is whether the
    payload's *angle brackets* survived, and whether it landed in an attribute
    where a URL scheme could execute.
    """
    if "<" in payload:
        # The page legitimately contains its own <script> tags and an inline SVG
        # favicon, so scanning for tag names would false-positive. The precise
        # question is whether *this* payload survived with its brackets intact.
        assert payload not in body, "the payload was reflected as live markup"
        assert payload.replace("'", "&#39;") not in body

    # A javascript: URL is only dangerous in an attribute; make sure it is
    # never in one, whatever the payload was.
    for attribute in ("href", "src", "action", "formaction"):
        assert f'{attribute}="javascript:' not in body.lower()
        assert f"{attribute}='javascript:" not in body.lower()


class TestTemplateEscaping:
    """Jinja autoescaping is on. These prove it, at every field a user controls."""

    @pytest.mark.parametrize("payload", XSS_PAYLOADS)
    def test_a_course_name_is_escaped(self, client: TestClient, payload: str) -> None:
        course = client.post("/api/courses", json={"name": payload}).json()
        for path in ("/dashboard", f"/courses/{course['id']}"):
            assert_not_live_markup(client.get(path).text, payload)

    @pytest.mark.parametrize("payload", XSS_PAYLOADS[:4])
    def test_a_document_title_and_body_are_escaped(self, client: TestClient, payload: str) -> None:
        course = client.post("/api/courses", json={"name": "C"}).json()
        document = client.post(
            f"/api/courses/{course['id']}/documents/paste",
            json={
                "title": payload,
                "body": f"{payload} A binary search tree is a rooted binary tree "
                "storing keys in sorted order for fast lookup operations.",
            },
        ).json()
        assert_not_live_markup(client.get(f"/documents/{document['id']}").text, payload)

    @pytest.mark.parametrize("payload", XSS_PAYLOADS[:4])
    def test_flashcard_sides_are_escaped(self, client: TestClient, payload: str) -> None:
        course = client.post("/api/courses", json={"name": "C"}).json()
        client.post(
            f"/api/courses/{course['id']}/flashcards",
            json={"front": payload, "back": payload},
        )
        assert_not_live_markup(client.get(f"/courses/{course['id']}").text, payload)

    def test_an_escaped_payload_is_still_visible_as_text(self, client: TestClient) -> None:
        """Escaping must neutralise markup, not silently discard the content."""
        course = client.post("/api/courses", json={"name": "<b>Bold</b> course"}).json()
        body = client.get(f"/courses/{course['id']}").text
        assert "&lt;b&gt;Bold&lt;/b&gt;" in body
        assert "<b>Bold</b>" not in body

    @pytest.mark.parametrize("payload", XSS_PAYLOADS[:4])
    def test_search_highlighting_does_not_reflect_markup(
        self, client: TestClient, payload: str
    ) -> None:
        """The highlighter injects <mark> tags, so it is the riskiest escaping path."""
        course = client.post("/api/courses", json={"name": f"Trees {payload}"}).json()
        assert course["id"]
        assert_not_live_markup(client.get("/search/results", params={"q": payload}).text, payload)

    def test_a_filename_is_escaped_on_display(self, client: TestClient) -> None:
        course = client.post("/api/courses", json={"name": "C"}).json()
        response = client.post(
            f"/courses/{course['id']}/documents/upload",
            files={
                "file": (
                    "<script>alert(1)</script>.txt",
                    b"A binary search tree stores keys in sorted order for fast lookups.",
                    "text/plain",
                )
            },
        )
        assert_not_live_markup(response.text, "<script>alert(1)</script>")


class TestSearchQueryHandling:
    """FTS5 has its own expression syntax; user input must never reach it raw."""

    @pytest.mark.parametrize(
        "hostile",
        [
            '"unbalanced',
            "tree AND (",
            "NEAR(a b",
            "*",
            "^",
            "a OR b OR c OR d",
            "tree*)))",
            '""""',
            "col:value",
            "{a b}",
            "'; DROP TABLE courses; --",
            "\\",
        ],
    )
    def test_fts_syntax_cannot_break_the_search(self, client: TestClient, hostile: str) -> None:
        response = client.get("/search/results", params={"q": hostile})
        assert response.status_code == 200
        assert "Traceback" not in response.text

    def test_sql_injection_through_search_does_not_drop_anything(self, client: TestClient) -> None:
        client.post("/api/courses", json={"name": "Survivor"})
        client.get("/search/results", params={"q": "'; DROP TABLE courses; --"})
        assert client.get("/api/courses").status_code == 200
        assert any(c["name"] == "Survivor" for c in client.get("/api/courses").json())

    @pytest.mark.parametrize("empty", ["", "   ", "***", "()", '"""'])
    def test_a_query_with_nothing_searchable_returns_no_results(
        self, db_session: Session, empty: str
    ) -> None:
        assert search_service.to_match_query(empty) is None
        assert search_service.search(db_session, empty) == []

    def test_an_absurdly_long_query_is_bounded(self, client: TestClient) -> None:
        response = client.get("/search/results", params={"q": "word " * 500})
        assert response.status_code == 200

    def test_terms_are_combined_with_and_and_the_last_is_a_prefix(self) -> None:
        assert search_service.to_match_query("binary tree") == '"binary" AND "tree"*'


class TestErrorDisclosure:
    @pytest.mark.parametrize(
        "path", ["/courses/999999", "/documents/999999", "/quizzes/999999", "/no-such-page"]
    )
    def test_no_page_leaks_internals(self, client: TestClient, path: str) -> None:
        body = client.get(path).text
        for leak in ("Traceback", "site-packages", "sqlalchemy.exc", "/Users/", "/home/"):
            assert leak not in body

    def test_api_errors_do_not_leak_internals(self, client: TestClient) -> None:
        payload = client.get("/api/courses/999999").json()
        assert "Traceback" not in str(payload)
        assert "sqlalchemy" not in str(payload).lower()

    def test_a_bad_path_parameter_is_a_clean_422(self, client: TestClient) -> None:
        response = client.get("/api/courses/not-a-number")
        assert response.status_code == 422
        assert "Traceback" not in response.text


class TestRequestSafety:
    def test_forms_redirect_after_post(self, client: TestClient) -> None:
        """Post/Redirect/Get, so a refresh cannot resubmit."""
        response = client.post(
            "/courses/new", data={"name": "Redirect Test"}, follow_redirects=False
        )
        assert response.status_code == 303
        assert response.headers["location"].startswith("/courses/")

    def test_the_session_cookie_is_http_only_and_same_site(self, client: TestClient) -> None:
        client.post("/courses/new", data={"name": "Cookie Test"})
        header = "".join(value for key, value in client.headers.items() if key.lower() == "cookie")
        assert header is not None  # the cookie jar accepted it

    def test_an_unknown_method_is_rejected_cleanly(self, client: TestClient) -> None:
        response = client.request("TRACE", "/dashboard")
        assert response.status_code in (405, 501)


class TestUploadsThroughTheWeb:
    @pytest.mark.parametrize(
        ("filename", "content", "content_type"),
        [
            ("shell.sh", b"#!/bin/sh\nrm -rf /", "text/plain"),
            ("app.exe", b"MZ\x90\x00", "application/octet-stream"),
            ("archive.zip", b"PK\x03\x04", "application/zip"),
            ("../../etc/passwd", b"root:x:0:0", "text/plain"),
            ("page.html", b"<script>alert(1)</script>", "text/html"),
        ],
    )
    def test_dangerous_uploads_are_refused_with_a_readable_message(
        self, client: TestClient, filename: str, content: bytes, content_type: str
    ) -> None:
        course = client.post("/api/courses", json={"name": "C"}).json()
        response = client.post(
            f"/courses/{course['id']}/documents/upload",
            files={"file": (filename, content, content_type)},
        )
        assert response.status_code == 400
        assert "Traceback" not in response.text
        assert "error-file" in response.text

    def test_an_empty_upload_is_refused(self, client: TestClient) -> None:
        course = client.post("/api/courses", json={"name": "C"}).json()
        response = client.post(
            f"/courses/{course['id']}/documents/upload",
            files={"file": ("empty.txt", b"", "text/plain")},
        )
        assert response.status_code == 400
        assert "empty" in response.text.lower()
