# Sovereign Enterprise AI Orchestration & Management Platform

A self-hosted, agentic AI platform for project and delivery management. Ask a question in plain
language ("Is PHX going to make the sprint? Who is overloaded?") and an agent answers it by calling
governed tools over **Jira, GitHub, Keka (HRMS), Gmail, your knowledge base and project analytics**,
with citations. It runs entirely on local LLMs, so no data leaves your infrastructure.

Writes need human approval. Access is role-based, every action is recorded in a tamper-evident
audit log, and guardrails redact personal data and detect prompt injection before any model sees
tool output.

## Features

| Area | What you get |
|---|---|
| **Agent** | LangGraph planner → executor → verifier → responder over two local models (operations + reporting), streamed to the UI step by step, answers cited `[S1]` back to tool results |
| **Integrations** | **36 systems**, all configured from `.env` ([docs/integrations.md](docs/integrations.md)): Jira, GitHub, Keka, Gmail; Linear, GitLab, Azure DevOps, Asana, ClickUp, Bitbucket; PagerDuty, Opsgenie, Sentry, Jenkins, Argo CD, SonarQube; BambooHR, Zoho People, Darwinbox, Workday; Zoho Books, Tally, SAP; Salesforce, HubSpot; Confluence, Notion, SharePoint, Google Drive; Google Calendar, Outlook; Zoom, Teams and Meet transcripts; Slack and Teams chat. Each has a **demo twin**, so everything works without credentials |
| **MCP server** | Claude Desktop, IDEs and other agents use the governed tools in **code mode** (`search_tools` + sandboxed `execute_code`) with personal access tokens; see [docs/mcp.md](docs/mcp.md) |
| **Planning intelligence** | Monte Carlo **digital twin** (chance to hit a date, P50/P80/P95, what-ifs), **negotiation assistant**, **predictive slip model** trained on your own history, **earned value** (CPI, EAC), production health, customer commitments |
| **Why is it late?** | From Jira history, comments, pull requests, leave, incidents and e-mail: **person-days lost per cause** (following dependencies to the root cause, e.g. a vendor), issue timelines, and **status claims checked against the data** on the day they were made — the real reason, not the status report |
| **Delivery lifecycle** | **Critical-path roadmap** (dependencies + one task per person, slack, late items), **DORA metrics** with bands and evidence, **release notes** and **retrospectives** from Jira/GitHub/CI facts, **requirements → Jira backlog** with history-based estimates, sent only through approvals |
| **Org intelligence** | **Knowledge graph** (who knows what, impact of someone's absence, bus factor), **AI chief of staff** (weekly decisions, drafted escalations, spoken briefing), **multi-agent** mode (supervisor + specialist agents), **compliance autopilot** (SOC 2 / ISO 27001 evidence pack) |
| **Governance** | Human approval for agent writes (optional four-eyes, TTL), RBAC with five roles, SHA-256 hash-chained audit log, redaction (secrets, cards, Aadhaar, PAN, SSN, IBAN), injection scanning |
| **Knowledge base** | Upload PDF/DOCX/Markdown; hybrid BM25 + vector search with RRF and optional reranking; **access control applied in SQL before ranking**, so the agent never sees a document the user can't |
| **Analytics** | Project health: sprint forecast, workload vs. capacity (Keka leave aware), contradiction detection (e.g. "Done" in Jira with failing CI), risk score; scheduled weekly reports |
| **Operations** | Per-user daily token quotas, rate limits, Prometheus metrics, OpenTelemetry traces, Grafana dashboard |
| **Quality** | ~700 tests (96% coverage), offline evaluation suites enforced as CI gates (retrieval, RAG, guardrails, analytics, planning calibration, red-team, MCP sandbox), plus an LLM-judged agent suite |

## Architecture

```mermaid
flowchart LR
  UI[React UI] -->|REST + SSE| API[FastAPI]
  API --> Agent[Agent graph<br/>planner · executor · verifier · responder]
  Agent --> GW[Tool gateway<br/>permission → validation → approval → audit]
  GW --> Jira & GitHub & Keka & Gmail
  GW --> KB[Knowledge search]
  GW --> AN[Analytics]
  Agent --> LLM[(Local LLMs<br/>Ollama / vLLM)]
  API --> DB[(PostgreSQL + pgvector)]
  API -. metrics/traces .-> OBS[Prometheus · Jaeger · Grafana]
```

The backend is a modular monolith organized by domain (`identity`, `agent`, `governance`,
`knowledge`, `analytics`, `reporting`, `usage`). See [docs/architecture.md](docs/architecture.md)
and [ADR 0001](docs/adr/0001-domain-modules.md).

## Quick start

### Option 1 — Docker Compose (everything)

Needs Docker with Compose v2.

```bash
docker compose up -d --build                 # Postgres + API + web, demo data seeded
```

Open http://localhost:8080 and sign in as `admin@example.com` (also `pm@`, `lead@`, `member@`,
`auditor@example.com`) with password `Demo!Passw0rd123` (override with `DEMO_PASSWORD`).

Optional profiles:

```bash
docker compose --profile ai up -d            # + Ollama, pulls LLM1_MODEL_NAME (default qwen3:8b)
docker compose --profile monitoring up -d    # + Prometheus :9090, Grafana :3000, Jaeger :16686
```

Without the `ai` profile (or an `LLM1_MODEL_NAME` pointing at a reachable server) everything works
except the agent endpoints, which return 503. To enable tracing set
`OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318`. For real integrations set `DEMO_MODE=false` and
the `JIRA_*` / `GITHUB_*` / `KEKA_*` / `GMAIL_*` variables in `.env` (see [.env.example](.env.example)).

### Option 2 — Local development

Needs Python 3.13 and Node 22.

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt   # Linux/macOS: .venv/bin/python
npm --prefix frontend ci
.venv/Scripts/python.exe -m scripts.dev demo     # API on :8000 with SQLite demo data and users
npm --prefix frontend run dev                    # UI on http://localhost:5173
```

For the agent, install [Ollama](https://ollama.com), `ollama pull qwen3:8b` and set
`LLM1_MODEL_NAME=qwen3:8b` before starting the API.

### Option 3 — Kubernetes (Helm)

PostgreSQL with pgvector is external. Create a Secret with `DATABASE_URL` and `JWT_SECRET_KEY`, then:

```bash
helm install sovereign deploy/helm/sovereign \
  --set image.api.repository=ghcr.io/your-org/sovereign-api \
  --set image.web.repository=ghcr.io/your-org/sovereign-web \
  --set ingress.enabled=true --set ingress.host=sovereign.example.com
```

Migrations run as a pre-install/pre-upgrade hook. See [values.yaml](deploy/helm/sovereign/values.yaml).

## Connecting your systems

Every integration is switched on by filling in its variables in `.env` (all of them are listed in
[.env.example](.env.example)) and restarting. Nothing else changes.

```bash
python -m scripts.connections            # what is connected, and which variables the rest need
python -m scripts.connections --check    # call each configured service
python -m scripts.sync_documents --as admin@company.com   # pull Confluence/Notion/SharePoint/Drive
python -m scripts.mcp_stdio              # MCP over stdio (SOVEREIGN_MCP_TOKEN=sov_pat_...)
```

Admins see the same status, with setup hints and API links, under **Admin → Integrations**. Each
project's `links` (in its configuration) tie it to its PagerDuty services, Sentry projects,
SonarQube keys, cost centres and so on.

## Configuration

All settings are environment variables, documented in [.env.example](.env.example). The important ones:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | `postgresql://…` in production, `sqlite:///./data/app.db` locally |
| `JWT_SECRET_KEY` | Required (32+ chars) outside development |
| `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM1_MODEL_NAME`, `LLM2_MODEL_NAME` | Local models (Ollama or any OpenAI-compatible server) |
| `EMBEDDING_MODEL_NAME` | Embeddings for search; empty = offline hashing embedder |
| `DEMO_MODE` | Serve every integration from the synthetic dataset |
| `APPROVAL_ALLOW_SELF_APPROVAL`, `APPROVAL_EXEMPT_TOOLS` | Approval policy |
| `AGENT_DAILY_TOKEN_QUOTA` | Per-user daily token budget |
| `METRICS_TOKEN`, `OTEL_EXPORTER_OTLP_ENDPOINT` | Protect `/metrics`; export traces |

Create the first administrator on a fresh database:

```bash
python -m alembic upgrade head
python -m scripts.create_admin --email you@company.com --name "Your Name"
```

## Roles

| Role | Can |
|---|---|
| `admin` | Everything, including user management |
| `project_manager` | Team lead permissions plus managing knowledge-base documents |
| `team_lead` | Member permissions plus project writes, email drafts, people/leave data, analytics, report generation and approving agent actions |
| `member` | Run the agent over projects, email and shared documents; read reports |
| `auditor` | Read-only: projects, analytics, reports, documents and the audit log; cannot run the agent |

The agent acts with the user's permissions intersected with its own scopes, so it can never do more
than the person asking.

## Development

```bash
.venv/Scripts/python.exe -m scripts.dev check    # lint, format, frontend types, all tests, build
.venv/Scripts/python.exe -m scripts.dev test tests/unit/core   # pytest args pass through
.venv/Scripts/python.exe -m scripts.dev fmt      # ruff --fix + format
npm --prefix frontend test                       # Vitest
pre-commit install                               # ruff, secret-file blocker, type check
```

PostgreSQL-specific tests (migrations, pgvector search) run when `TEST_POSTGRES_URL` points at a
disposable database, as they do in CI:

```bash
TEST_POSTGRES_URL=postgresql://sovereign:sovereign@localhost:5432/sovereign_test pytest -m postgres
```

## Evaluation

```bash
.venv/Scripts/python.exe -m scripts.dev eval                         # offline suites → evaluation/reports/
.venv/Scripts/python.exe -m evaluation run --suite agent --judge     # real model + LLM judge
```

Nine suites, each with its own dataset, thresholds and bootstrap 95% confidence intervals:
retrieval (hit rate, MRR, graded nDCG, context precision, latency, leak rate, a BM25/vector/hybrid
ablation), RAG (context recall/precision, faithfulness, answer relevance, semantic similarity,
factual correctness, citations), agent (tool F1, trajectory, argument accuracy, approval
compliance, negative rejection, latency, tokens), the delay engine (140 synthetic sprints with
planted causes, false-contradiction rate), forecasting calibration, guardrails, red team and MCP
sandbox. Ragas/DeepEval export and Phoenix-compatible traces included. CI fails if any gate
regresses. Full method, results and limitations: [docs/evaluation.md](docs/evaluation.md).

## Monitoring

- `GET /metrics` exposes Prometheus metrics: HTTP by route, agent runs and duration, tool calls,
  LLM latency and tokens, approvals and rate limiting.
- Traces cover each request, `agent.run`, `tool.invoke` and `llm.request`. They are exported over
  OTLP/HTTP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set, and logs carry the `trace_id`.
- The Compose `monitoring` profile provisions Prometheus, Jaeger and a Grafana "Sovereign overview" dashboard.

## Security

- **Local models only**: prompts and data stay on your servers.
- **Short-lived JWTs**: refresh tokens rotate, and reusing an old one revokes every session.
- **Approval for writes**: agent writes wait for a human. The approver needs both `approvals:decide`
  and the action's own permission.
- **Untrusted tool output**: it is redacted, truncated, scanned for injection and wrapped as
  untrusted data before a model sees it. A flagged run loses its approval exemptions.
- **Tamper-evident audit log**: entries are append-only and hash-chained.
- **Gmail creates drafts, never sends**: recipients can be limited by domain, and GitHub access by repository.
- **Hardened containers**: they run as non-root, Compose binds ports to localhost by default, and nginx keeps `/metrics` private.

## Project structure

```
backend/app/        api/ · core/ · db/ · domains/ · integrations/ · tools/ · llm/
backend/migrations/ Alembic
frontend/           React + TypeScript + Vite
evaluation/         offline + agent evaluation suites and datasets
tests/              unit/ · integration/ · evaluation/ · support/
deploy/             docker/ · monitoring/ · helm/
scripts/            dev runner, demo, admin creation, Gmail consent
docs/               architecture, ADRs, roadmap
```

## Roadmap

Phases 1–10 are complete, including every integration and moonshot in the backlog (LoRA fine-tuning
aside), plus most lifecycle features (backlog planning, estimation, critical path, DORA, release notes,
retrospectives). Next: project intake, staffing, standup digest and enterprise readiness; everything is
tracked in [docs/ROADMAP.md](docs/ROADMAP.md).
