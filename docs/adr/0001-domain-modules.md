# ADR 0001 — Organize the backend by domain (modular monolith)

- Status: accepted
- Date: 2026-11-09

## Context

The first phases grew a layer-first tree (`services/`, `schemas/`, `orchestration/`, `agents/`,
`retrieval/`, `reasoning/`, `workflows/`, …) plus ~30 empty placeholder packages. Related code
for one feature was spread across up to five folders, which made the project hard to navigate,
review and test, and blurred which modules may depend on which.

## Decision

- Group business logic under `backend/app/domains/<area>/` with that area's models, schemas
  and services together (identity, agent, governance, knowledge, analytics, reporting, usage,
  system).
- Keep cross-cutting code in `core/`, persistence plumbing in `db/`, external systems in
  `integrations/`, agent-callable capabilities in `tools/`, and HTTP in `api/v1/`.
- Keep `backend.app.db.models` as a registry that imports every domain model, so Alembic and
  tests see the full metadata and imports like `from backend.app.db.models import User` work.
- Remove placeholder packages; a package exists only when it has code.
- Mirror the layout in `tests/unit/<area>/` and in the frontend (`shared/`, `features/<area>/`).

## Consequences

- One place to look per feature; new contributors can map UI → API → domain → tool quickly.
- Clear dependency direction (documented in `docs/architecture.md`).
- A domain could later be extracted into its own service with little churn.
- All import paths changed once (no external consumers existed, so the cost was minimal).
