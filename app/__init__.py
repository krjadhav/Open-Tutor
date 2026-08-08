"""FastAPI backend for Open Tutor.

`app.server` implements `docs/api-contract.md` exactly; `app.state` holds the in-memory demo
session. Nothing in this package decides mastery, correctness or blame: the engine, the CAS and
the diagnosis cache own those, and the server only turns their output into UI-ready values.
"""
