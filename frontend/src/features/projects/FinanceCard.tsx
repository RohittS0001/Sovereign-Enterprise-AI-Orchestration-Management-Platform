import type { ProjectHealth } from "../../shared/api/types";
import { Badge, Card } from "../../shared/ui/components";

const money = (value: number | null, currency: string | null) =>
  value === null ? "—" : `${Math.round(value).toLocaleString()}${currency ? ` ${currency}` : ""}`;

/** Earned value: budget vs actual vs work done (finance connectors + Jira progress). */
export function FinanceCard({ finance: f }: { finance: NonNullable<ProjectHealth["finance"]> }) {
  const tone = f.cpi === null ? "unknown" : f.cpi >= 0.95 ? "healthy" : f.cpi >= 0.8 ? "warn" : "bad";
  return (
    <Card title="Budget (earned value)">
      <div className="row">
        <span className="stat">{f.cpi === null ? "—" : f.cpi.toFixed(2)}</span>
        <Badge value={tone} label="CPI" />
      </div>
      <div className="small">
        Budget {money(f.budget_at_completion, f.currency)} · spent {money(f.actual_cost, f.currency)} · earned{" "}
        {money(f.earned_value, f.currency)}
        {f.percent_complete !== null && ` (${Math.round(f.percent_complete * 100)}% complete)`}
      </div>
      {f.estimate_at_completion !== null && (
        <div className="small muted">
          Estimate at completion {money(f.estimate_at_completion, f.currency)} (variance{" "}
          {money(f.variance_at_completion, f.currency)})
        </div>
      )}
      {f.by_currency && Object.keys(f.by_currency).length > 1 && (
        <div className="small">
          {Object.entries(f.by_currency)
            .map(([cur, v]) => `${cur}: budget ${money(v.budget, null)} · spent ${money(v.actual, null)}`)
            .join(" | ")}
        </div>
      )}
      {f.note && <div className="small muted">{f.note}</div>}
      <div className="small muted">Source: {f.sources.join(", ")}</div>
    </Card>
  );
}

/** Customer deals (CRM) that depend on this project, soonest first. */
export function CommitmentsCard({ deals }: { deals: NonNullable<ProjectHealth["commitments"]> }) {
  return (
    <Card title={`Customer commitments (${deals.length})`}>
      {deals.length === 0 ? (
        <p className="small muted">No open deals depend on this project.</p>
      ) : (
        <ul className="list small">
          {deals.map((d) => (
            <li key={`${d.source}-${d.id}`}>
              {d.url ? <a href={d.url} target="_blank" rel="noreferrer">{d.name}</a> : <strong>{d.name}</strong>}{" "}
              <span className="muted">
                {d.stage} · {d.amount ? Math.round(d.amount).toLocaleString() : "—"} · closes {d.close_date ?? "—"} ({d.source})
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
