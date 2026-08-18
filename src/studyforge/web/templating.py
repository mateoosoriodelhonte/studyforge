"""Jinja2 environment, filters and the flash-message helper.

Autoescaping is on for every template. Any place a template deliberately emits
raw markup carries a comment explaining why -- there are two, both for inline
SVG icons defined in this codebase, never for user or model content.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

FlashLevel = Literal["success", "error", "info"]


def build_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    env = templates.env
    env.autoescape = True
    env.trim_blocks = True
    env.lstrip_blocks = True
    env.filters["relative_time"] = relative_time
    env.filters["absolute_time"] = absolute_time
    env.filters["duration"] = humanise_days
    env.filters["percent"] = format_percent
    env.filters["plural"] = pluralise
    env.filters["highlight"] = highlight
    env.globals["now"] = lambda: datetime.now(UTC)
    return templates


# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------


def relative_time(value: datetime | None, *, reference: datetime | None = None) -> str:
    """ "in 3 days" / "2 hours ago". Always paired with an exact timestamp in
    the markup's ``title`` attribute, so nothing is hidden behind vagueness."""
    if value is None:
        return "never"
    reference = reference or datetime.now(UTC)
    value = _as_utc(value)
    seconds = (value - reference).total_seconds()
    future = seconds > 0
    seconds = abs(seconds)

    if seconds < 45:
        return "just now" if not future else "in a moment"
    for limit, divisor, unit in (
        (3600, 60, "minute"),
        (86_400, 3600, "hour"),
        (2_592_000, 86_400, "day"),
        (31_536_000, 2_592_000, "month"),
    ):
        if seconds < limit:
            amount = max(1, round(seconds / divisor))
            label = f"{amount} {pluralise(amount, unit)}"
            return f"in {label}" if future else f"{label} ago"
    years = max(1, round(seconds / 31_536_000))
    label = f"{years} {pluralise(years, 'year')}"
    return f"in {label}" if future else f"{label} ago"


def absolute_time(value: datetime | None) -> str:
    if value is None:
        return ""
    return _as_utc(value).strftime("%d %b %Y, %H:%M UTC")


def humanise_days(days: float | None) -> str:
    if days is None:
        return "-"
    if days < 1:
        return "today"
    if days < 30:
        return f"{round(days)}d"
    if days < 365:
        return f"{days / 30.44:.1f}mo".replace(".0", "")
    return f"{days / 365.25:.1f}y".replace(".0", "")


def format_percent(value: float | None, *, places: int = 0) -> str:
    """Render a proportion, or an explicit dash when there is no data.

    Never renders 0% for missing data; that would be a lie dressed as a
    measurement.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{value * 100:.{places}f}%"


def pluralise(count: float, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else (plural or f"{singular}s")


def highlight(text: str, query: str) -> Markup:
    """Mark occurrences of ``query`` in ``text``.

    Both sides are escaped first and only the ``<mark>`` tags are added
    afterwards, so a search for ``<script>`` highlights the literal text rather
    than injecting anything.
    """
    if not query or not text:
        # Safe: the argument is the output of escape(), so nothing unescaped
        # can reach the browser. Ruff cannot see through the call.
        return Markup(escape(text))  # noqa: S704
    haystack, needle = text.lower(), query.lower()
    out: list[str] = []
    cursor = 0
    while (found := haystack.find(needle, cursor)) != -1:
        out.append(str(escape(text[cursor:found])))
        out.append(f"<mark>{escape(text[found : found + len(needle)])}</mark>")
        cursor = found + len(needle)
    out.append(str(escape(text[cursor:])))
    # Safe: every element of `out` is either escape()'d user text or a literal
    # <mark> tag written here. The only markup introduced is our own.
    return Markup("".join(out))  # noqa: S704


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# --------------------------------------------------------------------------
# Flash messages
# --------------------------------------------------------------------------

_FLASH_KEY = "flashes"


def flash(request: Request, message: str, level: FlashLevel = "info") -> None:
    """Queue a one-shot message for the next rendered page."""
    request.session.setdefault(_FLASH_KEY, []).append({"message": message, "level": level})


def consume_flashes(request: Request) -> list[dict[str, str]]:
    """Read and clear queued messages."""
    messages: list[dict[str, str]] = request.session.pop(_FLASH_KEY, [])
    return messages


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def render(
    templates: Jinja2Templates,
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
) -> Response:
    """Render a template with the shared context every page needs."""
    payload: dict[str, Any] = {
        "request": request,
        "flashes": consume_flashes(request),
        **(context or {}),
    }
    response: Response = templates.TemplateResponse(request, name, payload, status_code=status_code)
    return response
