"""The JSON API.

Deliberately a *subset* of what the web interface can do. Every HTMX
interaction is not forced through a public REST endpoint — that would produce a
sprawling API shaped by UI accidents rather than by anything a client wants.

What is here is the coherent read-and-write surface for the domain: courses,
documents, flashcards, reviews and progress. It is the part another program
could reasonably build on, and it is what FastAPI's generated OpenAPI docs
describe.
"""

from studyforge.api.router import api_router

__all__ = ["api_router"]
