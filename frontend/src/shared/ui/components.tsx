import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import { ApiError } from "../api/client";

export function Card({ title, actions, children }: { title?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      {(title || actions) && (
        <header className="card-header">
          {title && <h2>{title}</h2>}
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

const TONES: Record<string, string> = {
  success: "good", completed: "good", executed: "good", on_track: "good", low: "good", balanced: "good",
  live: "good", healthy: "good",
  pending: "warn", pending_approval: "warn", awaiting_approval: "warn", at_risk: "warn", medium: "warn",
  underused: "info", running: "info", demo: "info",
  error: "bad", failed: "bad", denied: "bad", rejected: "bad", off_track: "bad", high: "bad",
  overloaded: "bad", expired: "muted", disabled: "muted", unknown: "muted", warn: "warn",
};

export function Badge({ value, label }: { value: string; label?: string }) {
  return <span className={`badge badge-${TONES[value] ?? "muted"}`}>{label ?? value.replace(/_/g, " ")}</span>;
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="spinner" role="status">
      <span className="spinner-dot" aria-hidden /> {label}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const message =
    error instanceof ApiError ? error.message : error instanceof Error ? error.message : "Something went wrong";
  return (
    <div className="error-note" role="alert">
      {message}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

/** Markdown without raw HTML (react-markdown escapes it by default). */
export function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown>{text}</ReactMarkdown>
    </div>
  );
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
