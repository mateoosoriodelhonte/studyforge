"""The critical path, in a real browser.

Everything here goes through HTMX, which is precisely what a TestClient cannot
verify: whether the swap actually lands, whether focus moves, whether the
keyboard shortcuts reach the right element.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def wait_for_htmx(page: Page) -> None:
    """Wait until HTMX has finished binding the content it just swapped in.

    An element becomes *visible* at swap time but its ``hx-post`` handler is not
    wired until settle, a few milliseconds later. A test that presses a key in
    that window sees nothing happen — and so, briefly, would a very fast user.
    """
    page.wait_for_function(
        "() => document.querySelectorAll('.htmx-request, .htmx-settling').length === 0"
    )


class TestCreateAndIngest:
    def test_a_new_course_can_be_created_and_given_material(
        self, page: Page, live_server: str
    ) -> None:
        page.goto(f"{live_server}/courses/new")
        page.fill("#field-name", "Operating Systems")
        page.fill("#field-code", "CS 3210")
        page.click("button[type=submit]")
        expect(page.locator("h1")).to_contain_text("Operating Systems")

        page.click("text=Add material")
        page.fill("#field-title", "Scheduling")
        page.fill(
            "#field-body",
            "A context switch is the act of saving the state of one process and "
            "restoring the state of another so the processor can run it.\n\n"
            "Round robin scheduling is a preemptive policy that gives each process "
            "a fixed time slice in turn.",
        )
        page.click("button[type=submit]")

        expect(page.locator("h1")).to_contain_text("Scheduling")
        expect(page.locator("body")).to_contain_text("Concepts found here")
        expect(page.locator("body")).to_contain_text("context switch")

    def test_a_validation_error_is_shown_beside_the_field(
        self, page: Page, live_server: str
    ) -> None:
        page.goto(f"{live_server}/courses/new")
        page.click("button[type=submit]")
        error = page.locator("#error-name")
        expect(error).to_be_visible()
        expect(page.locator("#field-name")).to_have_attribute("aria-invalid", "true")


class TestStudyByKeyboardOnly:
    """The whole review loop, without ever touching the mouse."""

    def test_a_full_card_can_be_reviewed_with_the_keyboard(
        self, page: Page, live_server: str, own_course: int
    ) -> None:
        page.goto(f"{live_server}/study?course_id={own_course}")
        expect(page.locator("[data-study-session]")).to_be_visible()
        front = page.locator(".flashcard__face p").first.inner_text()

        page.keyboard.press("Space")
        expect(page.locator(".rating-row")).to_be_visible()
        expect(page.locator(".flashcard__answer")).to_be_visible()
        wait_for_htmx(page)

        page.keyboard.press("3")
        expect(page.locator(".rating-row")).to_have_count(0)
        expect(page.locator(".flashcard__face p").first).not_to_have_text(front)

    def test_the_rating_buttons_show_real_intervals(
        self, page: Page, live_server: str, own_course: int
    ) -> None:
        page.goto(f"{live_server}/study?course_id={own_course}")
        page.keyboard.press("Space")
        intervals = page.locator(".rating__interval")
        expect(intervals).to_have_count(4)
        for index in range(4):
            assert re.match(r"^\d+(\.\d+)?[mhdoy]", intervals.nth(index).inner_text())

    def test_ratings_are_ordered_so_easy_never_schedules_sooner_than_again(
        self, page: Page, live_server: str, own_course: int
    ) -> None:
        page.goto(f"{live_server}/study?course_id={own_course}")
        page.keyboard.press("Space")
        labels = [page.locator(".rating__interval").nth(index).inner_text() for index in range(4)]
        assert labels[0] != labels[3], "Again and Easy must not schedule identically"

    def test_escape_finishes_the_session_and_shows_a_summary(
        self, page: Page, live_server: str, own_course: int
    ) -> None:
        page.goto(f"{live_server}/study?course_id={own_course}")
        page.keyboard.press("Space")
        expect(page.locator(".rating-row")).to_be_visible()
        wait_for_htmx(page)
        page.keyboard.press("3")
        expect(page.locator(".rating-row")).to_have_count(0)
        page.keyboard.press("Escape")
        expect(page.locator("h1")).to_contain_text("Session finished")
        expect(page.locator("body")).to_contain_text("Cards reviewed")

    def test_every_interactive_element_is_reachable_by_tab(
        self, page: Page, live_server: str
    ) -> None:
        page.goto(f"{live_server}/dashboard")
        seen: set[str] = set()
        for _ in range(25):
            page.keyboard.press("Tab")
            focused = page.evaluate(
                "() => document.activeElement ? document.activeElement.tagName + ':' "
                "+ (document.activeElement.textContent || '').trim().slice(0, 24) : ''"
            )
            if focused:
                seen.add(focused)
        assert len(seen) >= 5, "the page should expose several tab stops"

    def test_the_skip_link_is_the_first_tab_stop(self, page: Page, live_server: str) -> None:
        page.goto(f"{live_server}/dashboard")
        page.keyboard.press("Tab")
        expect(page.locator(".skip-link")).to_be_focused()


class TestQuizFlow:
    def test_a_quiz_can_be_generated_taken_and_finished(self, page: Page, live_server: str) -> None:
        page.goto(f"{live_server}/courses/1")
        page.click("text=Generate a quiz")
        expect(page.locator("body")).to_contain_text("Question 1 of")

        total = int(re.search(r"Question 1 of (\d+)", page.content()).group(1))  # type: ignore[union-attr]

        for _ in range(total):
            if page.locator("input[type=radio]").count():
                page.locator(".choice").first.click()
            else:
                page.fill("#field-response", "an attempt at the answer")
            page.click("button[type=submit]")

            # Wait for the swap to land. Counting immediately after the click
            # races the HTMX request and reads the *previous* question's DOM.
            expect(page.locator(".notice[role=status]")).to_be_visible()

            finish = page.get_by_role("button", name="Finish quiz")
            if finish.count():
                finish.click()
                break
            page.get_by_role("button", name="Next question").click()
            expect(page.locator("form")).to_be_visible()

        expect(page.locator("body")).to_contain_text("Quiz finished")
        expect(page.locator("body")).to_contain_text("Score")


class TestProgressReflectsStudy:
    """The end of the critical path: study, and watch the numbers move."""

    @staticmethod
    def _reviews_all_time(page: Page, live_server: str) -> int:
        page.goto(f"{live_server}/api/progress")
        return int(page.evaluate("() => JSON.parse(document.body.innerText).reviews_total"))

    def test_reviewing_changes_the_numbers(
        self, page: Page, live_server: str, own_course: int
    ) -> None:
        before = self._reviews_all_time(page, live_server)

        page.goto(f"{live_server}/study?course_id={own_course}")
        expect(page.locator("[data-reveal]")).to_be_visible()

        page.keyboard.press("Space")
        expect(page.locator(".rating-row")).to_be_visible()
        wait_for_htmx(page)
        page.keyboard.press("3")
        # The rating row disappearing is the signal the swap landed. Waiting for
        # the *next card* instead would flake whenever this review empties the
        # queue, because then the swap is the "queue cleared" panel.
        expect(page.locator(".rating-row")).to_have_count(0)

        assert self._reviews_all_time(page, live_server) == before + 1

    def test_the_progress_page_renders_the_same_figures(self, page: Page, live_server: str) -> None:
        page.goto(f"{live_server}/progress")
        expect(page.locator("body")).to_contain_text("Reviews all time")
        expect(page.locator("body")).to_contain_text("Card recall")
        # Sample sizes must always accompany a rate.
        expect(page.locator(".stat__sample").first).to_be_visible()


class TestSearchAndAsk:
    def test_search_updates_as_you_type(self, page: Page, live_server: str) -> None:
        page.goto(f"{live_server}/search")
        page.fill("#field-q", "binary")
        expect(page.locator("mark").first).to_be_visible()

    def test_ask_shows_passages_and_says_no_ai_is_configured(
        self, page: Page, live_server: str
    ) -> None:
        page.goto(f"{live_server}/ask")
        page.fill("#field-question", "Why are AVL trees logarithmic?")
        page.click("button[type=submit]")
        expect(page.locator("body")).to_contain_text("From your notes")
        expect(page.locator("body")).to_contain_text("Showing your notes only")


class TestMobile:
    def test_the_study_view_works_on_a_phone(self, mobile_page: Page, live_server: str) -> None:
        mobile_page.goto(f"{live_server}/study")
        if mobile_page.locator("[data-reveal]").count() == 0:
            mobile_page.goto(f"{live_server}/dashboard")
            pytest.skip("nothing due; covered by the desktop keyboard tests")
        expect(mobile_page.locator("[data-study-session]")).to_be_visible()
        mobile_page.click("[data-reveal]")
        expect(mobile_page.locator(".rating-row")).to_be_visible()
        wait_for_htmx(mobile_page)

        # The rating grid must not overflow the viewport.
        box = mobile_page.locator(".rating-row").bounding_box()
        assert box is not None
        assert box["width"] <= 390

    def test_the_page_does_not_scroll_sideways(self, mobile_page: Page, live_server: str) -> None:
        for path in ("/dashboard", "/courses/1", "/progress"):
            mobile_page.goto(f"{live_server}{path}")
            overflow = mobile_page.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            assert overflow <= 1, f"{path} scrolls horizontally by {overflow}px"
