import type { TokenPair } from "./types";

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";
const REFRESH_KEY = "sovereign.refresh";

/** Error carrying the backend's {error: {code, message, details}} envelope. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
  ) {
    super(message);
  }
}

// The access token lives only in memory. The refresh token is kept in sessionStorage so
// a page reload keeps the session, but closing the tab ends it.
let accessToken: string | null = null;
let refreshInFlight: Promise<boolean> | null = null;
let onSessionExpired: () => void = () => {};

export function setSessionExpiredHandler(handler: () => void) {
  onSessionExpired = handler;
}

function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function storeTokens(tokens: TokenPair | null) {
  accessToken = tokens?.access_token ?? null;
  const store = storage();
  if (!store) return;
  if (tokens) store.setItem(REFRESH_KEY, tokens.refresh_token);
  else store.removeItem(REFRESH_KEY);
}

export function getRefreshToken(): string | null {
  return storage()?.getItem(REFRESH_KEY) ?? null;
}

export function hasAccessToken(): boolean {
  return accessToken !== null;
}

async function parseError(response: Response): Promise<ApiError> {
  try {
    const body = await response.json();
    const error = body?.error ?? {};
    return new ApiError(response.status, error.code ?? "error", error.message ?? response.statusText, error.details);
  } catch {
    return new ApiError(response.status, "error", response.statusText || "Request failed");
  }
}

/** Exchange the stored refresh token for new tokens (one request at a time). */
export async function refreshSession(): Promise<boolean> {
  const token = getRefreshToken();
  if (!token) return false;
  refreshInFlight ??= (async () => {
    try {
      const response = await fetch(`${BASE}/api/v1/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: token }),
      });
      if (!response.ok) {
        storeTokens(null);
        return false;
      }
      storeTokens(await response.json());
      return true;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

export interface RequestOptions {
  method?: string;
  body?: unknown;
  form?: FormData;
  signal?: AbortSignal;
}

/** Fetch with auth; retries once after refreshing an expired access token. */
export async function rawRequest(path: string, options: RequestOptions = {}, retried = false): Promise<Response> {
  const headers: Record<string, string> = {};
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  let body: BodyInit | undefined;
  if (options.form) body = options.form;
  else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }

  const response = await fetch(`${BASE}/api/v1${path}`, {
    method: options.method ?? (body ? "POST" : "GET"),
    headers,
    body,
    signal: options.signal,
  });

  if (response.status === 401 && !retried && !path.startsWith("/auth/")) {
    if (await refreshSession()) return rawRequest(path, options, true);
    onSessionExpired();
  }
  if (!response.ok) throw await parseError(response);
  return response;
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await rawRequest(path, options);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
