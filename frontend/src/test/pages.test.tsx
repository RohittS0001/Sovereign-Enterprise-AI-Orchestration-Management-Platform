import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Approval, CurrentUser, IntegrationStatus } from "../shared/api/types";
import { json, LEAD, renderApp, signedInAs } from "./utils";

const ADMIN: CurrentUser = {
  ...LEAD,
  id: "u0",
  email: "admin@example.com",
  role: "admin",
  permissions: [...LEAD.permissions, "users:manage", "audit:read", "knowledge:manage"],
};
const MEMBER: CurrentUser = { ...LEAD, id: "u2", role: "member", permissions: ["agent:query", "projects:read"] };

const APPROVAL: Approval = {
  id: "a1",
  run_id: "run-1",
  requested_by_id: "u2",
  tool_name: "jira.create_task",
  permission: "projects:write",
  arguments: { project_key: "PHX", summary: "Escalate vendor outage" },
  summary: "Create task",
  risk_flags: ["possible_prompt_injection"],
  status: "pending",
  created_at: "2026-11-09T10:00:00Z",
  expires_at: "2026-11-12T10:00:00Z",
  decided_by_id: null,
  decided_at: null,
  decision_note: null,
  result: null,
  error: null,
};

const page = <T,>(items: T[]) => json({ items, total: items.length, limit: 50, offset: 0 });

describe("Approvals page", () => {
  it("shows flagged requests and approves with a note", async () => {
    const fetchMock = signedInAs(LEAD, {
      "GET /approvals": () => page([APPROVAL]),
      "POST /approvals/a1/approve": () => json({ ...APPROVAL, status: "executed" }),
    });
    renderApp("/approvals");

    expect(await screen.findByText("jira.create_task")).toBeInTheDocument();
    expect(screen.getByText(/Flagged: possible prompt injection/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Note (optional)"), "ok to escalate");
    await userEvent.click(screen.getByRole("button", { name: "Approve and run" }));

    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/approvals/a1/approve"))).toBe(true),
    );
    const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/approvals/a1/approve"))!;
    expect(JSON.parse(String(call[1]!.body))).toEqual({ note: "ok to escalate" });
  });

  it("does not offer decisions to people without approvals:decide", async () => {
    signedInAs(MEMBER, { "GET /approvals": () => page([APPROVAL]) });
    renderApp("/approvals");

    expect(await screen.findByText("jira.create_task")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve and run" })).not.toBeInTheDocument();
  });
});

describe("Audit page", () => {
  it("lists events and reports chain integrity", async () => {
    signedInAs(ADMIN, {
      "GET /audit": () =>
        page([
          {
            seq: 1,
            occurred_at: "2026-11-09T10:00:00Z",
            actor_type: "human",
            actor_user_id: "u0",
            action: "auth.login",
            resource_type: null,
            resource_id: null,
            status: "success",
            details: {},
          },
        ]),
      "GET /audit/verify": () => json({ valid: true, checked: 1, first_invalid_seq: null, reason: null }),
    });
    renderApp("/audit");

    expect(await screen.findByRole("cell", { name: "auth.login" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Verify integrity" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Chain intact — 1 events verified.");
  });
});

describe("Knowledge page", () => {
  it("searches and shows cited results with how they matched", async () => {
    signedInAs(LEAD, {
      "GET /knowledge/documents": () => page([]),
      "POST /knowledge/search": () =>
        json({
          results: [
            {
              document_id: "d1",
              title: "Incident Escalation Policy",
              section: "Vendor incidents",
              citation: "Incident Escalation Policy › Vendor incidents",
              score: 0.9,
              matched_by: ["semantic", "keyword"],
              text: "Escalate to the vendor manager within 4 hours.",
            },
          ],
        }),
    });
    renderApp("/knowledge");

    await userEvent.type(await screen.findByLabelText("Search documents"), "vendor no fix date");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByText("Incident Escalation Policy › Vendor incidents")).toBeInTheDocument();
    expect(screen.getByText("semantic + keyword")).toBeInTheDocument();
  });
});

describe("MCP access page", () => {
  it("creates a token, shows it once and lists it", async () => {
    const token = {
      id: "t1", name: "Claude Desktop", prefix: "sov_pat_abc123", created_at: "2026-11-09T10:00:00Z",
      expires_at: "2026-12-09T10:00:00Z", last_used_at: null,
    };
    let created = false;
    const fetchMock = signedInAs(LEAD, {
      "GET /tokens": () => json(created ? [token] : []),
      "POST /tokens": () => {
        created = true;
        return json({ ...token, token: "sov_pat_abc123SECRET" }, 201);
      },
    });
    renderApp("/mcp-access");

    expect(await screen.findByText("No active tokens.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Create token" }));

    expect(await screen.findByRole("status")).toHaveTextContent("sov_pat_abc123SECRET");
    expect(await screen.findByText("sov_pat_abc123…")).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith("/tokens") && init?.method === "POST")!;
    expect(JSON.parse(String(post[1]!.body))).toEqual({ name: "Claude Desktop", expires_in_days: 30 });
  });
});

describe("Delivery twin", () => {
  it("runs a what-if simulation and shows the negotiation options", async () => {
    const sim = (scenario: string, p: number, p80: string) => ({
      scenario, target_date: "2026-11-20", probability_by_target: p, p50: "2026-11-19", p80, p95: "2026-11-25",
      finish_rate: 1, remaining_points: 24, remaining_issues: 5, efficiency: 0.8, blocked_issues: [],
      rework_chance: 0.1, histogram: [["2026-11-19", 500], ["2026-11-24", 500]], trials: 1000,
      drivers: ["1 open issue(s) are blocked (PHX-3)"],
    });
    const health = {
      project: "PHX", name: "Phoenix", as_of: "2026-11-09", warnings: [], issue_counts: {},
      risk: { score: 40, level: "medium", factors: [] }, forecast: null, contradictions: [],
      workload: { period_end: "2026-11-14", members: [], unassigned_open: [], suggestions: [] },
    };
    const fetchMock = signedInAs(LEAD, {
      "GET /projects/PHX/health": () => json(health),
      "POST /projects/PHX/simulate": () => json([sim("baseline", 0.5, "2026-11-24"), sim("what-if", 0.9, "2026-11-19")]),
      "POST /projects/PHX/negotiate": () =>
        json({
          target_date: "2026-11-20", baseline: sim("baseline", 0.5, "2026-11-24"), recommended: "add_one",
          options: [{ id: "add_one", title: "Add one person", tradeoff: "costs budget", meets_goal: true,
                      result: sim("add_one", 0.85, "2026-11-20") }],
          message_markdown: "We recommend adding one person.", generated_by_model: null,
        }),
    });
    renderApp("/projects/PHX");

    await userEvent.click(await screen.findByRole("tab", { name: "Forecast & what-ifs" }));
    await userEvent.type(await screen.findByLabelText("Add people"), "1");
    await userEvent.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("90%")).toBeInTheDocument();
    expect(screen.getAllByRole("img", { name: /Likely finish 2026-11-19/ })).toHaveLength(2);
    const body = JSON.parse(String(fetchMock.mock.calls.find(([u]) => String(u).endsWith("/simulate"))![1]!.body));
    expect(body.scenarios[0]).toMatchObject({ add_people: 1 });

    await userEvent.click(screen.getByRole("button", { name: "Negotiation options" }));
    expect(await screen.findByText("recommended")).toBeInTheDocument();
    expect(screen.getByText("We recommend adding one person.")).toBeInTheDocument();
  });
});

describe("Chief-of-staff brief", () => {
  it("lists decisions, speaks the briefing and drafts escalations", async () => {
    const speak = vi.fn();
    Object.assign(window, { speechSynthesis: { speak, cancel: vi.fn() } });
    vi.stubGlobal("SpeechSynthesisUtterance", class { constructor(public text: string) {} });
    signedInAs(LEAD, {
      "GET /projects": () => json([{ key: "PHX", name: "Phoenix", github_repos: [], members: [], active_sprint_id: 42, is_active: true }]),
      "GET /briefing": () =>
        json({ as_of: "2026-11-09", projects: [], decisions: ["PHX: approve 'Move the date'"], spoken_text: "Good morning. Phoenix is at risk." }),
      "POST /briefing/escalations": () => json([{ project: "PHX", status: "pending_approval" }]),
    });
    renderApp("/projects");

    expect(await screen.findByText("PHX: approve 'Move the date'")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Play briefing" }));
    expect(speak).toHaveBeenCalledOnce();
    expect((speak.mock.calls[0][0] as { text: string }).text).toBe("Good morning. Phoenix is at risk.");
    await userEvent.click(screen.getByRole("button", { name: "Draft escalations" }));
    expect(await screen.findByRole("status")).toHaveTextContent("PHX: draft waiting for approval");
    vi.unstubAllGlobals();
  });
});

describe("Integrations page", () => {
  it("shows which integrations are connected and which env vars the rest need", async () => {
    const integrations: IntegrationStatus[] = [
      {
        name: "jira", title: "Jira Cloud", category: "core", mode: "live", tools: ["jira.get_issue"],
        missing_env: [], setup: "", docs_url: "", healthy: null, detail: null,
      },
      {
        name: "github", title: "GitHub", category: "core", mode: "disabled", tools: [],
        missing_env: ["GITHUB_TOKEN"], setup: "Fine-grained token", docs_url: "https://docs.github.com/rest",
        healthy: null, detail: null,
      },
    ];
    signedInAs(ADMIN, { "GET /integrations": () => json(integrations) });
    renderApp("/integrations");

    const github = (await screen.findByText("GitHub")).closest("li")!;
    expect(within(github).getByText("GITHUB_TOKEN")).toBeInTheDocument();
    expect(within(github).getByRole("link", { name: "API docs" })).toHaveAttribute("href", "https://docs.github.com/rest");
    expect(screen.getByText(/1 of 2 connected/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Core" }));
    await userEvent.type(screen.getByLabelText("Filter integrations"), "jira");
    expect(screen.queryByText("GitHub")).not.toBeInTheDocument();
  });

  it("is not reachable without users:manage", async () => {
    signedInAs(LEAD, { "GET /agent/runs": () => page([]) });
    renderApp("/admin");

    expect(await screen.findByText("You don't have access to this page.")).toBeInTheDocument();
  });
});

describe("Compliance page", () => {
  it("summarises controls and opens the ones needing attention", async () => {
    signedInAs(ADMIN, {
      "GET /compliance/report": () =>
        json({ generated_at: "2026-11-09T10:00:00Z", summary: { pass: 1, warn: 0, fail: 0, unknown: 0 },
               controls: [{ id: "logging", title: "Event logging and monitoring", soc2: ["CC7.2"], iso27001: ["A.8.15"],
                            status: "pass", evidence: ["Audit log hash chain verified: 1 events, intact"], gaps: [] }] }),
    });
    renderApp("/compliance");

    expect(await screen.findByText("Event logging and monitoring")).toBeInTheDocument();
    expect(screen.getByText("Passing").closest("section")).toHaveTextContent("1");
  });
});

describe("Home dashboard", () => {
  it("shows portfolio KPIs, project risk and the decisions to take", async () => {
    signedInAs(LEAD, {
      "GET /briefing": () =>
        json({
          as_of: "2026-11-09", spoken_text: "Good morning.", decisions: ["PHX: approve 'Move the date'"],
          projects: [{ key: "PHX", name: "Phoenix", risk_score: 81, risk_level: "high", top_risks: ["Blocked work: PHX-3 blocked"],
                       chance_by_target: 0.12, target_date: "2026-11-14", likely_finish: "2026-12-09",
                       decision_needed: "PHX: approve 'Move the date'" }],
        }),
      "GET /approvals": () => json({ items: [], total: 3, limit: 50, offset: 0 }),
      "GET /agent/runs": () => json({ items: [], total: 0, limit: 5, offset: 0 }),
    });
    renderApp("/home");

    expect(await screen.findByRole("link", { name: "Phoenix" })).toHaveAttribute("href", "/projects/PHX");
    expect(screen.getByRole("img", { name: "Score 81 of 100" })).toBeInTheDocument();
    expect(screen.getByText("High-risk projects").closest("section")).toHaveTextContent("1");
    expect(screen.getByText("Pending approvals").closest("section")).toHaveTextContent("3");
    expect(screen.getByText("12%")).toBeInTheDocument();
  });
});

describe("People page", () => {
  it("finds experts and assesses the impact of an absence", async () => {
    const peopleLead = { ...LEAD, permissions: [...LEAD.permissions, "people:read"] };
    signedInAs(peopleLead, {
      "GET /graph/summary": () => json({ people: 7, items: 17, edges: 23, bus_factor: { "acme/web": 1 } }),
      "GET /graph/who-knows": () => json([{ person: "Arjun M", score: 10, evidence: ["issue PHX-1: idempotency (done)"] }]),
      "GET /graph/people/Arjun%20M/impact": () =>
        json({ person: "Arjun M", severity: "medium", sole_open_work: ["PHX-5: Refund webhooks"], blocked_behind: ["PHX-3"],
               concentrated_repositories: [], documents_owned: [], suggested_backups: [{ person: "Meera S", score: 5, evidence: [] }] }),
    });
    renderApp("/people");

    expect(await screen.findByText("acme/web")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Topic"), "refunds");
    await userEvent.click(screen.getByRole("button", { name: "Find experts" }));
    expect(await screen.findByText("issue PHX-1: idempotency (done)")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Person"), "Arjun M");
    await userEvent.click(screen.getByRole("button", { name: "Assess impact" }));
    expect(await screen.findByText("medium impact")).toBeInTheDocument();
    expect(screen.getByText("PHX-5: Refund webhooks")).toBeInTheDocument();
    expect(screen.getByText("Meera S")).toBeInTheDocument();
  });
});

describe("Shell", () => {
  it("opens the command palette with Ctrl+K and navigates", async () => {
    signedInAs(LEAD, { "GET /agent/runs": () => page([]) });
    renderApp("/assistant");

    await screen.findByRole("heading", { name: "Assistant" });
    await userEvent.keyboard("{Control>}k{/Control}");
    const search = await screen.findByLabelText("Search commands");
    await userEvent.type(search, "knowledge");
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByRole("heading", { name: "Knowledge" })).toBeInTheDocument();
  });

  it("toggles the theme", async () => {
    signedInAs(LEAD, { "GET /agent/runs": () => page([]) });
    renderApp("/assistant");

    const toggle = await screen.findByRole("button", { name: /Switch to (dark|light) theme/ });
    const before = document.documentElement.dataset.theme;
    await userEvent.click(toggle);
    expect(document.documentElement.dataset.theme).not.toBe(before);
  });
});

describe("Delivery lifecycle tabs", () => {
  const health = {
    project: "PHX", name: "Phoenix", as_of: "2026-11-09", warnings: [], issue_counts: {},
    risk: { score: 40, level: "medium", factors: [] }, forecast: null, contradictions: [],
    workload: { period_end: "2026-11-14", members: [], unassigned_open: [], suggestions: [] },
  };
  const item = (key: string, start: number, finish: number, critical: boolean, extra = {}) => ({
    key, summary: `${key} work`, assignee: "Arjun M", status: "To Do", points: 3, duration_days: finish - start,
    start: "2026-11-09", finish: "2026-11-12", start_day: start, finish_day: finish, slack_days: critical ? 0 : 2.4,
    critical, depends_on: [], after: [], due: null, late_by_days: null, ...extra,
  });

  it("shows the critical path schedule and flags a late finish", async () => {
    signedInAs(LEAD, {
      "GET /projects/PHX/health": () => json(health),
      "GET /projects/PHX/roadmap": () => json({
        finish: "2026-11-19", total_working_days: 8.1, critical_path: ["PHX-7", "PHX-5"], external_blockers: {},
        late_vs_sprint: true, sprint_end: "2026-11-14", notes: ["In-progress issues are assumed to be half done."],
        items: [item("PHX-7", 0, 2, true, { late_by_days: 3 }), item("PHX-2", 0, 4, false), item("PHX-5", 2, 8.1, true)],
      }),
    });
    renderApp("/projects/PHX");

    await userEvent.click(await screen.findByRole("tab", { name: "Roadmap" }));
    expect(await screen.findByText("PHX-7 → PHX-5")).toBeInTheDocument();
    expect(screen.getByText("8 days")).toBeInTheDocument();
    expect(screen.getByText(/finishes after the sprint ends/)).toBeInTheDocument();
    expect(screen.getByText("3d past due")).toBeInTheDocument();
    expect(screen.getByText("2 days slack")).toBeInTheDocument();
  });

  it("shows DORA bands and switches the window", async () => {
    const metric = (name: string, value: number | null, unit: string, band: string | null) =>
      ({ name, value, unit, band, evidence: value === null ? [] : [`${name} evidence`], source: "argocd", note: null });
    const fetchMock = signedInAs(LEAD, {
      "GET /projects/PHX/health": () => json(health),
      "GET /projects/PHX/dora": () => json({
        window_days: 30, overall: "high", ci_failure_rate: 0.67, recommendations: ["Fix the build first."],
        deployment_frequency: metric("Deployment frequency", 0.47, "per week", "medium"),
        lead_time: metric("Lead time for changes", 3.5, "days", "high"),
        change_failure_rate: metric("Change failure rate", 0, "%", "elite"),
        time_to_restore: metric("Time to restore service", null, "days", null),
      }),
    });
    renderApp("/projects/PHX");

    await userEvent.click(await screen.findByRole("tab", { name: "DORA metrics" }));
    expect(await screen.findByText("3.5 days")).toBeInTheDocument();
    expect(screen.getByText("0.47 / week")).toBeInTheDocument();
    expect(screen.getByText("no data")).toBeInTheDocument();
    expect(screen.getByText("Fix the build first.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Last 90 days" }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes("window_days=90"))).toBe(true),
    );
  });

  it("drafts a backlog, lets you edit it, and sends it for approval", async () => {
    const proposal = {
      drafted_by: "parser", total_points: 8, assumptions: [], open_questions: [], injection_warning: [],
      epics: [{ title: "Refund disputes", description: "", stories: [
        { title: "Open a dispute", description: "", acceptance_criteria: ["merchant notified"], points: 5,
          estimate_basis: "Similar to PHX-5 (5 pts).", similar_issues: ["PHX-5"] },
        { title: "Dispute queue", description: "", acceptance_criteria: [], points: 3,
          estimate_basis: "No similar issue; team median of 3 points.", similar_issues: [] },
      ] }],
    };
    const fetchMock = signedInAs(LEAD, {
      "GET /projects/PHX/health": () => json(health),
      "POST /projects/PHX/backlog/draft": () => json(proposal),
      "POST /projects/PHX/backlog/submit": () => json([
        { tool: "jira.create_epic", title: "Refund disputes", status: "pending_approval", approval_id: "a1", error: null },
        { tool: "jira.create_story", title: "Open a dispute", status: "pending_approval", approval_id: "a2", error: null },
      ]),
    });
    renderApp("/projects/PHX");

    await userEvent.click(await screen.findByRole("tab", { name: "Backlog planner" }));
    const draft = screen.getByRole("button", { name: "Draft backlog" });
    expect(draft).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Use an example" }));
    await userEvent.click(draft);
    expect(await screen.findByText("Similar to PHX-5 (5 pts).")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Remove Dispute queue" }));
    expect(screen.queryByText("Dispute queue")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Send to Jira for approval" }));
    expect(await screen.findByText(/2 of 2 item\(s\) are waiting for approval/)).toBeInTheDocument();

    const submit = fetchMock.mock.calls.find(([url]) => String(url).includes("/backlog/submit"))!;
    const body = JSON.parse(String(submit[1]!.body));
    expect(body.epics[0].stories.map((s: { title: string }) => s.title)).toEqual(["Open a dispute"]);
  });
});

describe("Why is it late?", () => {
  it("shows the main reason, contradicted claims and checks a statement", async () => {
    const health = {
      project: "PHX", name: "Phoenix", as_of: "2026-11-09", warnings: [], issue_counts: {},
      risk: { score: 81, level: "high", factors: [] }, forecast: null, contradictions: [],
      workload: { period_end: "2026-11-14", members: [], unassigned_open: [], suggestions: [] },
    };
    const claim = {
      claim: "Phoenix is on track for the sprint release.", source: "email: PHX weekly update",
      said_by: "priya.n@acme.example", said_on: "2026-11-07", verdict: "contradicted",
      reality: ["Projected finish 2026-11-19 is after the target 2026-11-14."],
    };
    const fetchMock = signedInAs(LEAD, {
      "GET /projects/PHX/health": () => json(health),
      "GET /projects/PHX/delay": () => json({
        sprint: "Sprint 42", target: "2026-11-14", projected_finish: "2026-11-19", slip_days: 3,
        headline: "Projected 3 working day(s) late. Main reason: waiting on an external party (PHX-3, PHX-7).",
        total_days_lost: 13.7, warnings: [], claims: [claim],
        losses: [{ cause: "blocked_external", label: "Waiting on an external party", days: 7, share: 0.51,
                   issues: ["PHX-3", "PHX-7"], evidence: ["PHX-7 blocked 3 working day(s): vendor regression"] }],
        stories: [{ key: "PHX-7", summary: "Vendor timeouts", assignee: "Arjun M", status: "Blocked", days_lost: 3,
                    segments: [{ status: "In Progress", start: "2026-11-01", end: "2026-11-04", days: 2 },
                               { status: "Blocked", start: "2026-11-04", end: "2026-11-09", days: 3 }],
                    events: ["2026-11-04: Arjun M moved it In Progress → Blocked"] }],
      }),
      "POST /projects/PHX/delay/check": () => json([{ ...claim, claim: "No blockers", said_by: "you were told" }]),
    });
    renderApp("/projects/PHX");

    await userEvent.click(await screen.findByRole("tab", { name: "Why is it late?" }));
    expect(await screen.findByText(/Main reason: waiting on an external party/)).toBeInTheDocument();
    expect(screen.getByText("3 working days late")).toBeInTheDocument();
    expect(screen.getByText("7d · 51%")).toBeInTheDocument();
    expect(screen.getByText("“Phoenix is on track for the sprint release.”")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "In Progress 2 days, Blocked 3 days" })).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("Statement to check"), "No blockers");
    await userEvent.click(screen.getByRole("button", { name: "Check against data" }));
    expect(await screen.findByText("“No blockers”")).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([url]) => String(url).includes("/delay/check"))!;
    expect(JSON.parse(String(post[1]!.body))).toEqual({ statement: "No blockers" });
  });
});

describe("Knowledge search over indexed platforms", () => {
  it("filters by source, shows where results come from and indexes project data", async () => {
    const pm = { ...LEAD, permissions: [...LEAD.permissions, "knowledge:read", "knowledge:manage"] };
    const fetchMock = signedInAs(pm, {
      "GET /knowledge/documents": () => json({ items: [], total: 0, limit: 50, offset: 0 }),
      "POST /knowledge/search": () => json({ query: "leadership", results: [{
        document_id: "d1", title: "slack C0PHX0001 · 2026-11-06", section: null, chunk: 0,
        citation: "slack C0PHX0001 · 2026-11-06", score: 0.2, matched_by: ["keyword"],
        source: "slack", project: "PHX", date: "2026-11-06",
        text: "Priya N: Let's keep reporting Phoenix as on track to leadership until Friday",
      }] }),
      "POST /knowledge/index": () => json([{ project: "PHX", sources: [
        { source: "jira", added: 8, updated: 0, unchanged: 0, removed: 0, error: null },
        { source: "slack", added: 6, updated: 0, unchanged: 0, removed: 0, error: null },
        { source: "chat", added: 0, updated: 0, unchanged: 0, removed: 0, error: "Slack is down" },
      ] }]),
    });
    renderApp("/knowledge");

    await userEvent.click(await screen.findByRole("button", { name: "Index project data" }));
    expect(await screen.findByText(/PHX: 14 new or updated, 0 unchanged across 2 sources \(chat unavailable\)/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Chat" }));
    await userEvent.type(screen.getByLabelText("Search documents"), "leadership");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText(/on track to leadership/)).toBeInTheDocument();
    expect(screen.getByText("Slack")).toBeInTheDocument();
    expect(screen.getByText(/PHX · 2026-11-06/)).toBeInTheDocument();

    const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/knowledge/search"))!;
    expect(JSON.parse(String(call[1]!.body)).sources).toEqual(["slack", "teams_chat"]);
  });
});

describe("Cloud monitoring", () => {
  const resource = (name: string, kind: string, health: string, extra = {}) => ({
    source: "aws", id: `arn:${name}`, name, kind, service: "ec2:instance", size: "m6i.large", region: "ap-south-1a",
    state: "running", health, health_detail: null, storage_gb: null, created: "2026-10-01", environment: "production",
    purpose: `${name} purpose`, owner: "Arjun M", url: null, ...extra,
  });

  it("shows a project's resources, where and why they run, health, storage, cost and findings", async () => {
    const health = {
      project: "PHX", name: "Phoenix", as_of: "2026-11-09", warnings: [], issue_counts: {},
      risk: { score: 81, level: "high", factors: [] }, forecast: null, contradictions: [],
      workload: { period_end: "2026-11-14", members: [], unassigned_open: [], suggestions: [] },
    };
    signedInAs(LEAD, {
      "GET /projects/PHX/health": () => json(health),
      "GET /projects/PHX/cloud": () => json({
        project: "PHX", clouds: ["aws", "azure"], warnings: [],
        resources: [
          resource("phx-api-1", "instance", "ok"),
          resource("phx-api-2", "instance", "impaired", { health_detail: "EC2 status check failing" }),
          resource("phx-payments", "database", "ok", { storage_gb: 200, size: "db.r6g.large postgres" }),
        ],
        counts: { instance: 2, database: 1 }, states: { ok: 2, impaired: 1 }, storage_gb: 200,
        storage_by_kind: { database: 200 }, owners: { "Arjun M": 3 },
        placements: [{ region: "ap-south-1a", environment: "production", resources: 3 }],
        cost: { currency: "USD", month_to_date: 1252.15, forecast_month_end: 2578, by_cloud: { aws: 1126.55, azure: 125.6 },
                by_service: [{ service: "Amazon EC2", amount: 612.4 }, { service: "Amazon RDS", amount: 401.25 }] },
        alerts: [{ source: "aws", id: "a1", title: "phx-api-2-status: StatusCheckFailed", severity: "high", state: "ALARM", resource: null, started: null }],
        findings: null, findings_by_severity: { critical: 1 },
      }),
    });
    renderApp("/projects/PHX");

    await userEvent.click(await screen.findByRole("tab", { name: "Cloud & infra" }));
    expect(await screen.findByText("phx-api-2 purpose")).toBeInTheDocument();
    expect(screen.getByText("EC2 status check failing")).toBeInTheDocument();
    expect(screen.getByText("3 active · 1 need attention")).toBeInTheDocument();
    expect(screen.getByText("Amazon EC2")).toBeInTheDocument();
    expect(screen.getByText("phx-api-2-status: StatusCheckFailed")).toBeInTheDocument();
    expect(screen.getByText("Security findings are visible to admins and auditors.")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "database (1)" }));
    expect(screen.queryByText("phx-api-1")).not.toBeInTheDocument();
    expect(screen.getByText("phx-payments")).toBeInTheDocument();
  });

  it("compares every project and shows who works on what", async () => {
    signedInAs(LEAD, {
      "GET /portfolio": () => json({
        as_of: "2026-11-09",
        totals: { projects: 2, resources: 12, unhealthy: 2, storage_gb: 1231, cost_month_to_date: 1999, cost_forecast: 4068, alerts: 3 },
        projects: [
          { project: "PHX", name: "Phoenix Payments Revamp", team: ["Arjun M", "Meera S"], resources: 10, active: 9, unhealthy: 2,
            storage_gb: 731, cost_month_to_date: 1252, cost_forecast: 2578, currency: "USD", alerts: 3, critical_findings: 1,
            regions: ["ap-south-1", "centralindia"], warnings: [] },
          { project: "LDG", name: "Ledger Modernisation", team: ["Kiran P", "Meera S"], resources: 2, active: 2, unhealthy: 0,
            storage_gb: 500, cost_month_to_date: 747, cost_forecast: 1490, currency: "USD", alerts: 0, critical_findings: 0,
            regions: ["ap-south-1"], warnings: [] },
        ],
        people: { "Arjun M": ["PHX"], "Kiran P": ["LDG"], "Meera S": ["LDG", "PHX"] },
      }),
    });
    renderApp("/portfolio");

    expect(await screen.findByRole("link", { name: "Phoenix Payments Revamp" })).toHaveAttribute("href", "/projects/PHX");
    expect(screen.getByText("1 critical")).toBeInTheDocument();
    expect(screen.getByText("2 unhealthy")).toBeInTheDocument();
    expect(screen.getByText("Meera S")).toBeInTheDocument();
    expect(screen.getByText("1.2 TB")).toBeInTheDocument();
  });
});
