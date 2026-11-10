import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import { ApiError } from "../api/client";
import { Icon, type IconName } from "./icons";

export function Card({
  title,
  actions,
  children,
  className = "",
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className}`.trim()}>
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

export function PageHeader({
  title,
  description,
  eyebrow,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  eyebrow?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-header">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}

/** A KPI tile: label, a big tabular number, an optional footnote. */
export function Kpi({ icon, label, value, foot, tone }: {
  icon?: IconName; label: string; value: ReactNode; foot?: ReactNode; tone?: "good" | "warn" | "bad";
}) {
  return (
    <section className="card kpi">
      <div className="kpi-label">
        {icon && <Icon name={icon} />}
        {label}
      </div>
      <div className={`kpi-value ${tone ? `tone-${tone}` : ""}`}>{value}</div>
      {foot && <div className="kpi-foot">{foot}</div>}
    </section>
  );
}

export function toneFor(score: number, warnAt: number, badAt: number): "good" | "warn" | "bad" {
  return score >= badAt ? "bad" : score >= warnAt ? "warn" : "good";
}

const TONE_COLOR = { good: "var(--good)", warn: "var(--warn)", bad: "var(--bad)" } as const;

/** Circular gauge (0–100), e.g. a risk score. */
export function Ring({ value, label, size = 88, tone }: {
  value: number; label?: string; size?: number; tone: "good" | "warn" | "bad";
}) {
  const stroke = 8;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div className="ring" style={{ width: size, height: size }} role="img" aria-label={`${label ?? "Score"} ${Math.round(clamped)} of 100`}>
      <svg width={size} height={size}>
        <circle className="ring-track" cx={size / 2} cy={size / 2} r={radius} fill="none" strokeWidth={stroke} />
        <circle className="ring-value" cx={size / 2} cy={size / 2} r={radius} fill="none" strokeWidth={stroke}
                stroke={TONE_COLOR[tone]} strokeLinecap="round"
                strokeDasharray={`${(clamped / 100) * circumference} ${circumference}`} />
      </svg>
      <div className="ring-label">
        <strong>{Math.round(clamped)}</strong>
        {label && <span>{label}</span>}
      </div>
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange, label }: {
  tabs: { id: T; label: string }[]; value: T; onChange: (id: T) => void; label: string;
}) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map((t) => (
        <button key={t.id} role="tab" type="button" className="tab" aria-selected={value === t.id} onClick={() => onChange(t.id)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Avatar({ name }: { name?: string | null }) {
  const initials = (name ?? "?")
    .split(/\s+/)
    .map((part) => part[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();
  return (
    <span className="avatar" aria-hidden="true">
      {initials}
    </span>
  );
}

const TONES: Record<string, string> = {
  success: "good", completed: "good", executed: "good", on_track: "good", low: "good", balanced: "good",
  live: "good", healthy: "good", pass: "good",
  pending: "warn", pending_approval: "warn", awaiting_approval: "warn", at_risk: "warn", medium: "warn",
  underused: "info", running: "info", demo: "info", info: "info", accent: "info", muted: "muted",
  error: "bad", failed: "bad", denied: "bad", rejected: "bad", off_track: "bad", high: "bad", fail: "bad",
  overloaded: "bad", expired: "muted", disabled: "muted", unknown: "muted", warn: "warn", bad: "bad", good: "good",
};

export function Badge({ value, label }: { value: string; label?: string }) {
  return (
    <span className={`badge badge-${TONES[value] ?? "muted"}`}>
      <span className="badge-text">{label ?? value.replace(/_/g, " ")}</span>
    </span>
  );
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

export function formatDay(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(`${value.slice(0, 10)}T00:00:00`);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
