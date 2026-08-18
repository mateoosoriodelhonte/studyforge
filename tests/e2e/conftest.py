"""End-to-end fixtures: a real server, a real browser, a real database.

The application runs in a background thread against a temporary database seeded
with the sample course, so these tests exercise the same code path a user does
-- including the HTMX round trips, which no TestClient assertion can cover.

Skipped cleanly when Playwright's browsers are not installed, so ``pytest``
stays runnable on a machine that has not run ``playwright install``.
"""

from __future__ import annotations

import socket
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx2 as httpx
import pytest

pytest.importorskip("playwright", reason="Playwright is not installed")

from playwright.sync_api import Browser, Error, Page, sync_playwright

from studyforge.config import Environment, Settings
from studyforge.db import create_db_engine, create_session_factory, session_scope
from studyforge.demo import seed_demo_course
from studyforge.fts import create_indexes
from studyforge.main import create_app
from studyforge.models import Base

GRAPH_NOTES = """
Spanning Trees

A spanning tree is a subgraph that connects every vertex of a graph without
forming any cycle at all.

A minimum spanning tree is a spanning tree whose total edge weight is no greater
than that of any other spanning tree of the same graph.

A cut is a partition of the vertices of a graph into two disjoint and non empty
subsets.
"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@pytest.fixture(scope="session")
def live_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    import uvicorn

    data_dir: Path = tmp_path_factory.mktemp("e2e-data")
    settings = Settings(
        environment=Environment.TEST,
        data_dir=data_dir,
        database_url=f"sqlite+pysqlite:///{data_dir / 'e2e.db'}",
        secret_key="e2e-only-secret-key",
        log_level="WARNING",
    )
    settings.ensure_directories()

    engine = create_db_engine(settings)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        create_indexes(connection)
    with session_scope(create_session_factory(engine)) as session:
        seed_demo_course(session)

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 20
    while time.time() < deadline:
        if server.started:
            break
        time.sleep(0.05)
    else:  # pragma: no cover - only on a very slow machine
        pytest.fail("the test server did not start in time")

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=10)
    engine.dispose()


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        try:
            instance = playwright.chromium.launch()
        except Error as error:  # pragma: no cover - environment dependent
            pytest.skip(f"Chromium is not installed: run `playwright install chromium` ({error})")
        yield instance
        instance.close()


@pytest.fixture
def page(browser: Browser, live_server: str) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    new_page = context.new_page()
    new_page.set_default_timeout(10_000)
    yield new_page
    context.close()


@pytest.fixture
def own_course(live_server: str) -> int:
    """A course with its own freshly generated, all-due cards.

    Study tests consume cards. Sharing the demo course between them would make
    them order-dependent -- whichever ran first would drain the queue -- so each
    test that studies gets material nobody else touches.

    Set up over HTTP rather than in the browser: it is the same public API, and
    it keeps a page of JavaScript out of a fixture.
    """
    with httpx.Client(base_url=live_server, timeout=20.0) as client:
        course = client.post("/api/courses", json={"name": f"E2E {uuid.uuid4().hex[:6]}"}).json()
        client.post(
            f"/api/courses/{course['id']}/documents/paste",
            json={"title": "Notes", "body": GRAPH_NOTES},
        )
        client.post(f"/api/courses/{course['id']}/flashcards/generate")
    course_id: int = course["id"]
    return course_id


@pytest.fixture
def mobile_page(browser: Browser, live_server: str) -> Iterator[Page]:
    context = browser.new_context(
        viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
    )
    new_page = context.new_page()
    new_page.set_default_timeout(10_000)
    yield new_page
    context.close()
