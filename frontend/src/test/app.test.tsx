import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { api, ApiError, storeTokens } from "../shared/api/client";
import { parseSseChunk } from "../shared/api/stream";
import { json, LEAD, mockBackend, renderApp, sse } from "./utils";

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

    // Leads land on the Home dashboard (they have analytics:read).
    expect(await screen.findByRole("heading", { level: 1, name: /^Good (morning|afternoon|evening), Team$/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Assistant" })).toBeInTheDocument();
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
