#!/usr/bin/env python
"""Capture the README screenshots from a running StudyForge.

Run against an instance seeded with the sample course, so the screenshots show
real output from the real pipeline rather than mocked-up content:

    uv run studyforge db init && uv run studyforge db demo
    uv run studyforge serve &
    uv run --group e2e python scripts/capture_screenshots.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:8000"
OUTPUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"

DESKTOP = {"width": 1360, "height": 940}
MOBILE = {"width": 390, "height": 844}

#: (filename, path, viewport, colour scheme, optional action)
SHOTS: list[tuple[str, str, dict[str, int], str, str | None]] = [
    ("dashboard.png", "/dashboard", DESKTOP, "dark", None),
    ("course.png", "/courses/1", DESKTOP, "dark", None),
    ("study.png", "/study", DESKTOP, "dark", "reveal"),
    ("progress.png", "/progress", DESKTOP, "dark", None),
    ("document.png", "/documents/1", DESKTOP, "light", None),
    ("settings.png", "/settings", DESKTOP, "light", None),
    ("study-mobile.png", "/study", MOBILE, "dark", "reveal"),
]


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for name, path, viewport, scheme, action in SHOTS:
            context = browser.new_context(
                viewport=viewport, color_scheme=scheme, device_scale_factor=2
            )
            page = context.new_page()
            page.goto(f"{BASE_URL}{path}", wait_until="networkidle")

            if action == "reveal":
                button = page.query_selector("[data-reveal]")
                if button is not None:
                    button.click()
                    page.wait_for_selector(".rating-row", timeout=5_000)

            page.screenshot(path=str(OUTPUT / name))
            print(f"captured {name}")
            context.close()
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
