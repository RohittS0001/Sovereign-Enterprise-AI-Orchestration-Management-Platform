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
      "GET /compliance/report": () =>
        json({ generated_at: "2026-11-09T10:00:00Z", summary: { pass: 1, warn: 0, fail: 0, unknown: 0 },
               controls: [{ id: "logging", title: "Event logging and monitoring", soc2: ["CC7.2"], iso27001: ["A.8.15"],
                            status: "pass", evidence: ["Audit log hash chain verified: 1 events, intact"], gaps: [] }] }),
    });
    renderApp("/audit");

    expect(await screen.findByRole("cell", { name: "auth.login" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Verify integrity" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Chain intact — 1 events verified.");
    expect(screen.getByText("Event logging and monitoring")).toBeInTheDocument();
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

describe("Admin page", () => {
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
    renderApp("/admin");

    const github = (await screen.findByText("GitHub")).closest("li")!;
    expect(within(github).getByText("GITHUB_TOKEN")).toBeInTheDocument();
    expect(within(github).getByRole("link", { name: "API docs" })).toHaveAttribute("href", "https://docs.github.com/rest");
    expect(screen.getByText(/1 of 2 connected/)).toBeInTheDocument();
  });

  it("is not reachable without users:manage", async () => {
    signedInAs(LEAD, { "GET /agent/runs": () => page([]) });
    renderApp("/admin");

    expect(await screen.findByText("You don't have access to this page.")).toBeInTheDocument();
  });
});
