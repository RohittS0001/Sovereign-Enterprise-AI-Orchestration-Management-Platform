import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { api, ApiError, storeTokens } from "../shared/api/client";
import { parseSseChunk } from "../shared/api/stream";
import type { CurrentUser } from "../shared/api/types";
import { AuthProvider } from "../shared/auth/AuthContext";

const LEAD: CurrentUser = {
  id: "u1",
  email: "lead@example.com",
  full_name: "Team Lead",
  role: "team_lead",
  is_active: true,
  permissions: ["agent:query", "projects:read", "analytics:read", "reports:read", "knowledge:read", "approvals:decide"],
  agent_permissions: [],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function sse(events: object[]): Response {
  const text = events.map((e) => `event: ${(e as { type: string }).type}\ndata: ${JSON.stringify(e)}\n\n`).join("");
  return new Response(text, { headers: { "Content-Type": "text/event-stream" } });
}

/** Route fetch calls by "METHOD /path" to canned responses. */
function mockBackend(routes: Record<string, () => Response>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const key = `${init?.method ?? "GET"} ${url.pathname.replace("/api/v1", "")}`;
    const handler = routes[key];
    if (!handler) return json({ error: { code: "not_found", message: `No mock for ${key}` } }, 404);
    return handler();
  });
}

function renderApp(path = "/", children: ReactNode = <App />) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>{children}</MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

describe("SSE parsing", () => {
  it("returns complete events and keeps the partial remainder", () => {
    const buffer = 'event: step\ndata: {"type":"step","n":1}\n\nevent: done\ndata: {"type":"do';
    const { events, rest } = parseSseChunk(buffer);
    expect(events).toEqual([{ type: "step", n: 1 }]);
    expect(rest).toBe('event: done\ndata: {"type":"do');
  });
});

describe("API client", () => {
  it("refreshes an expired access token once and retries", async () => {
    storeTokens({ access_token: "old", refresh_token: "r1", expires_in: 900 });
    let calls = 0;
    const fetchMock = mockBackend({
      "GET /projects": () => (++calls === 1 ? json({ error: { code: "token_expired", message: "expired" } }, 401) : json([])),
      "POST /auth/refresh": () => json({ access_token: "new", refresh_token: "r2", expires_in: 900 }),
    });

    await expect(api("/projects")).resolves.toEqual([]);

    const retry = fetchMock.mock.calls.at(-1)!;
    expect((retry[1]!.headers as Record<string, string>).Authorization).toBe("Bearer new");
    expect(sessionStorage.getItem("sovereign.refresh")).toBe("r2");
  });

  it("surfaces the backend error envelope", async () => {
    mockBackend({ "GET /usage": () => json({ error: { code: "quota_exceeded", message: "Daily quota reached" } }, 429) });

    const error = await api("/usage").catch((e) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 429, code: "quota_exceeded", message: "Daily quota reached" });
  });
});

describe("App", () => {
  it("signs in and shows navigation allowed by the user's permissions", async () => {
    mockBackend({
      "POST /auth/login": () => json({ access_token: "a", refresh_token: "r", expires_in: 900 }),
      "GET /auth/me": () => json(LEAD),
      "GET /agent/runs": () => json({ items: [], total: 0, limit: 15, offset: 0 }),
    });
    renderApp();

    await userEvent.type(await screen.findByLabelText("Email"), "lead@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "Str0ng!Passw0rd");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { name: "Assistant" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Projects" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Admin" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Audit log" })).not.toBeInTheDocument();
  });

  it("shows a login error from the backend", async () => {
    mockBackend({
      "POST /auth/login": () => json({ error: { code: "authentication_error", message: "Invalid email or password" } }, 401),
    });
    renderApp();

    await userEvent.type(await screen.findByLabelText("Email"), "x@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
  });

  it("streams agent steps and renders the answer with approvals", async () => {
    sessionStorage.setItem("sovereign.refresh", "r");
    const run = {
      run_id: "run-1",
      status: "awaiting_approval",
      query: "Is PHX at risk?",
      answer: "**PHX is high risk** because PHX-7 is blocked [S1].",
      citations: ["S1"],
      steps: [
        { id: "S1", tool: "analytics.project_health", purpose: "health", status: "success", error: null, approval_id: null, suspicious: false },
        { id: "S2", tool: "jira.create_task", purpose: "escalate", status: "pending_approval", error: null, approval_id: "a1", suspicious: false },
      ],
      pending_approvals: ["a1"],
      flagged_injection: false,
      error: null,
      usage: { prompt_tokens: 100, completion_tokens: 20 },
      started_at: "2026-11-09T10:00:00Z",
      finished_at: "2026-11-09T10:00:05Z",
    };
    mockBackend({
      "POST /auth/refresh": () => json({ access_token: "a", refresh_token: "r", expires_in: 900 }),
      "GET /auth/me": () => json(LEAD),
      "GET /agent/runs": () => json({ items: [], total: 0, limit: 15, offset: 0 }),
      "POST /agent/query/stream": () =>
        sse([
          { type: "run_started", run_id: "run-1" },
          { type: "step", step: { step_id: "S1", tool: "analytics.project_health", purpose: "health", status: "success", error: null, approval_id: null, suspicious: false } },
          { type: "phase", phase: "writing_answer" },
          { type: "done", run },
        ]),
    });
    renderApp("/assistant");

    await userEvent.type(await screen.findByLabelText("Question"), "Is PHX at risk?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));

    await waitFor(() => expect(screen.getByText("PHX is high risk")).toBeInTheDocument());
    expect(screen.getByText("jira.create_task")).toBeInTheDocument();
    expect(screen.getByText(/1 action\(s\) are waiting for approval/)).toBeInTheDocument();
    expect(screen.getByText("120 tokens", { exact: false })).toBeInTheDocument();
  });
});
