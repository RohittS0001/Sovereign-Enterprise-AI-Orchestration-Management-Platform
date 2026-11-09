# Architecture

A modular monolith: one FastAPI service organized by **domain**, a React frontend, and
locally hosted models. Everything an AI agent does passes through one governed tool gateway.

```
backend/app/
├── main.py              app factory: wires settings, DB, models, integrations, tools, routers
├── api/                 HTTP only — deps.py (auth, sessions, rate limits) + v1/<area>.py routers
├── core/                cross-cutting: config, logging, errors, error_handlers, context,
│                        middleware/ (request id, security headers), security/ (JWT, roles)
├── db/                  base (declarative base, mixins), session, types (UTCDateTime,
│                        EmbeddingVector), models/ (registry importing every domain model)
├── domains/             business logic, each with models / schemas / services
│   ├── identity/        users, login, refresh-token rotation
│   ├── agent/           LangGraph graph, nodes/ (planner, executor, verifier, responder,
│   │                    router), prompts/, state, run persistence + streaming events
│   ├── governance/      approvals/, audit/ (hash chain), guardrails/ (redaction, injection)
│   ├── knowledge/       parsing, chunking, embeddings, hybrid ranking, reranker, service
│   ├── analytics/       project config, snapshot collector, forecast, workload, risk,
│   │                    Jira↔GitHub contradictions
│   ├── reporting/       weekly report generator + scheduler
│   ├── usage/           token accounting and quotas
│   └── system/          health and readiness
├── integrations/        external systems: common/http base, jira, github, keka, gmail,
│                        demo/ (synthetic company), hub.py (live vs demo wiring)
├── tools/               what the agent can call: base (ToolResult), registry, catalog,
│                        gateway (permission → validation → approval → audit), runner,
│                        <integration>/ {schemas, tools}, knowledge, analytics
└── llm/                 model clients (Ollama, OpenAI-compatible), structured output, fake
backend/migrations/      Alembic
frontend/src/            shared/ (api client + SSE stream, auth, ui) · features/<area>/
tests/                   unit/<area>/ · integration/{api,agent,db,live}/ · support/ (fakes)
scripts/                 dev.py (task runner), run_demo, create_admin, seeds, gmail_auth
docs/                    architecture (this file), adr/, ROADMAP.md
```

## Dependency rules

- `api` → `domains`, `tools`, `core`, `db` (never the other way round).
- `domains` → `core`, `db`, `llm`, `tools` (the agent and approvals call the gateway/registry).
- `tools` → `integrations`, `domains` services needed by a tool (knowledge, analytics), `core`.
- `integrations` → `core` only. They know nothing about agents, users or the database.
- `db/models` is the only place that imports every domain model (for Alembic and tests).

## A question to the agent

```
POST /api/v1/agent/query[/stream]
  → api/v1/agent.py (auth, permission agent:query, rate limit, daily quota)
  → domains/agent/service.py  AgentRun row, streams events
  → domains/agent/graph.py    plan → execute → … → verify → respond
       planner (LLM 1, structured JSON over the permitted tool catalog)
       executor → tools/gateway.py → permission, validation, approval?, audit → registry
                → tools/<integration>/tools.py → integrations/<x>/service.py → client
       responder (LLM 2, grounded answer with [S#] citations)
  → AgentRun saved, audit event written
```

Writes proposed by the agent become `ApprovalRequest`s; approving executes the stored call.
See `docs/adr/` for the reasoning behind these choices.
