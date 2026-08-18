"""Application services.

The layer between HTTP handlers and the domain. Routes translate requests into
service calls and render the results; services own transactions, orchestration
and the rules that span more than one entity. Nothing here imports FastAPI, so
every service is callable and testable without a request.
"""
