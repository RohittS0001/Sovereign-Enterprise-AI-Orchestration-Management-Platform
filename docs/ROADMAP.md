# Roadmap & backlog

Status legend: `[x]` done · `[ ]` planned. Every new integration follows the tool chain in
`CLAUDE.md` (client → service → schemas → tools → hub/catalog → demo service) and every new
agent capability goes through the tool gateway (permissions, approvals, audit).

## Delivered

- [x] Phase 1 — Foundation: config, structured logging, error envelope, Jira/Gmail hardening
- [x] Phase 2 — Database, JWT auth with refresh rotation, roles & permissions
- [x] Phase 3 — LangGraph agent, human approvals, hash-chained audit log, guardrails
- [x] Phase 4 — GitHub & Keka integrations, Gmail drafts, demo mode
- [x] Phase 5 — Knowledge base: permission-aware hybrid search with citations
- [x] Phase 6 — Project analytics (risk, forecast, workload, contradictions), reports, usage quotas
- [x] Phase 7 — React frontend with streamed agent runs

## Next

- [x] Restructure into domain modules (easy to navigate, test and evaluate)
- [x] Phase 8 — Evaluation (golden sets, tool-call accuracy, RAG faithfulness, red-team, CI gates)
      and observability (OpenTelemetry traces, Prometheus metrics); dashboards ship with Phase 9
- [x] Phase 9 — Docker Compose (+ Prometheus/Grafana/Jaeger), GitHub Actions CI, Helm chart, README

## Backlog A — Integrations

- [x] **Slack / Microsoft Teams bot** — ask the agent, approve actions and receive digests in chat
- [x] **Document connectors for the knowledge base** — Confluence, Notion, SharePoint/OneDrive,
      Google Drive; incremental sync that mirrors each source's permissions
- [x] **Calendar & meetings** — Google Calendar / Outlook (availability, scheduling);
      Zoom / Google Meet / Teams transcripts → decisions and action items
- [x] **Other trackers** — Linear, Azure DevOps, Asana, ClickUp, GitLab, Bitbucket behind a
      common issue/PR model (no Jira lock-in)
- [x] **Engineering operations** — PagerDuty/Opsgenie incidents, Sentry errors, Jenkins/Argo
      CI/CD, SonarQube quality feeding the risk score
- [x] **HR & finance** — Darwinbox, Zoho People, BambooHR, Workday (alongside Keka);
      Zoho Books, Tally, SAP for budget and actuals (enables earned value management)
- [x] **CRM** — Salesforce / HubSpot: which customer commitments depend on which project
- [x] **MCP server** (code mode, see docs/mcp.md) — expose the platform's governed tools (permissions, approvals, audit) to
      Claude, IDEs and internal bots

## Backlog B — Moonshots

- [x] **Project digital twin** — Monte Carlo simulation over dependencies, leave, velocity and CI
      failure rates: "62% chance to ship by the 25th"; what-if scenarios (add a person, cut scope)
- [x] **Org knowledge graph** — people ↔ skills ↔ code ↔ issues ↔ documents ↔ decisions
      ("who knows the refunds service?", "what breaks if Arjun leaves?")
- [x] **AI chief of staff** — always-on agent watching every project, preparing weekly decisions,
      drafting escalations and scope trade-offs; everything through approvals
- [ ] **Predictive risk model** — learn early slip signals from your own history; optional LoRA
      fine-tuning on org data, kept on-premises
- [x] **Voice briefings** — "what's at risk today?" as a 2-minute spoken summary
- [x] **Negotiation assistant** — when a deadline is at risk, generate options (scope cut,
      added people, phased release) with simulated outcomes and draft stakeholder messages
- [ ] **Multi-agent org** — planner, QA, delivery and finance agents coordinated by a supervisor
      over one shared approval and audit layer
- [ ] **Compliance autopilot** — map work to ISO 27001 / SOC 2 controls and collect evidence
      from the audit log, PRs and CI

## Backlog C — Lifecycle features (from the same review)

- [ ] Project intake portal → AI-drafted project charter
- [ ] Requirements doc → proposed epics/stories/estimates in Jira (approval-gated)
- [ ] Estimation assistant from historical velocity and similar tickets
- [ ] Roadmap / Gantt / dependency graph with critical path
- [ ] Skill-based staffing (skills, code ownership, load, leave)
- [ ] Daily standup digest and proactive alerts
- [ ] Portfolio dashboard, earned value management, DORA metrics
- [ ] Retrospective generator, lessons-learned library, automatic release notes
- [ ] Enterprise readiness: SSO (OIDC/SAML) + SCIM, multi-tenancy, secrets vault, data
      retention / DPDP & GDPR tooling, SIEM export, LLMOps (prompt versions, model routing, costs)
