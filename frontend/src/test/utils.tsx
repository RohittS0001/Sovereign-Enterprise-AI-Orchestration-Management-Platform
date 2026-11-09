import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { App } from "../App";
import type { CurrentUser } from "../shared/api/types";
import { AuthProvider } from "../shared/auth/AuthContext";

export const LEAD: CurrentUser = {
  id: "u1",
  email: "lead@example.com",
  full_name: "Team Lead",
  role: "team_lead",
  is_active: true,
  permissions: ["agent:query", "projects:read", "analytics:read", "reports:read", "knowledge:read", "approvals:decide"],
  agent_permissions: [],
};

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export function sse(events: object[]): Response {
  const text = events.map((e) => `event: ${(e as { type: string }).type}\ndata: ${JSON.stringify(e)}\n\n`).join("");
  return new Response(text, { headers: { "Content-Type": "text/event-stream" } });
}

/** Route fetch calls by "METHOD /path" to canned responses; anything else is a 404 envelope. */
export function mockBackend(routes: Record<string, () => Response>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const key = `${init?.method ?? "GET"} ${url.pathname.replace("/api/v1", "")}`;
    const handler = routes[key];
    if (!handler) return json({ error: { code: "not_found", message: `No mock for ${key}` } }, 404);
    return handler();
  });
}

/** Start already signed in as `user` (a refresh token is present and /auth/me answers). */
export function signedInAs(user: CurrentUser, routes: Record<string, () => Response>) {
  sessionStorage.setItem("sovereign.refresh", "r");
  return mockBackend({
    "POST /auth/refresh": () => json({ access_token: "a", refresh_token: "r", expires_in: 900 }),
    "GET /auth/me": () => json(user),
    ...routes,
  });
}

export function renderApp(path = "/", children: ReactNode = <App />) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          {children}
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}
