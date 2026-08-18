"""Server-rendered web interface: Jinja2 templates driven by HTMX.

Business logic lives in :mod:`studyforge.services`. Routers here translate a
request into a service call and render the result -- nothing more. That
boundary is what lets the whole domain be tested without an HTTP client.
"""
