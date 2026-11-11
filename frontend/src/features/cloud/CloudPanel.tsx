import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../shared/api/client";
import { Badge, Card, Empty, ErrorNote, Kpi, Spinner, formatDate, formatDay } from "../../shared/ui/components";

export interface CloudResource {
  source: string;
  id: string;
  name: string;
  kind: string;
  service: string;
  size: string | null;
  region: string | null;
  state: string | null;
  health: "ok" | "warning" | "impaired" | "stopped" | "unknown";
  health_detail: string | null;
  storage_gb: number | null;
  created: string | null;
  environment: string | null;
  purpose: string | null;
  owner: string | null;
  url: string | null;
}

interface ProjectCloud {
  project: string;
  clouds: string[];
  resources: CloudResource[];
  counts: Record<string, number>;
  states: Record<string, number>;
  storage_gb: number;
  storage_by_kind: Record<string, number>;
  placements: { region: string; environment: string | null; resources: number }[];
  owners: Record<string, number>;
  cost: {
    currency: string;
    month_to_date: number;
    forecast_month_end: number | null;
    by_service: { service: string; amount: number }[];
    by_cloud: Record<string, number>;
  } | null;
  alerts: { source: string; id: string; title: string; severity: string | null; state: string; resource: string | null; started: string | null }[];
  findings: { source: string; id: string; title: string; severity: string; status: string; resource: string | null; recommendation: string | null }[] | null;
  findings_by_severity: Record<string, number>;
  warnings: string[];
}

export const HEALTH_TONE: Record<string, string> = { ok: "good", warning: "warn", impaired: "bad", stopped: "muted", unknown: "muted" };
const SEVERITY_TONE: Record<string, string> = { critical: "bad", high: "bad", medium: "warn", low: "info", informational: "muted" };
const CLOUD_LABEL: Record<string, string> = { aws: "AWS", azure: "Azure" };

export const money = (amount: number | null | undefined, currency = "USD") =>
  amount == null ? "—" : new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 0 }).format(amount);
export const gb = (value: number) => (value >= 1024 ? `${(value / 1024).toFixed(1)} TB` : `${Math.round(value)} GB`);

export function CloudPanel({ projectKey }: { projectKey: string }) {
  const cloud = useQuery({
    queryKey: ["cloud", projectKey],
    queryFn: () => api<ProjectCloud>(`/projects/${encodeURIComponent(projectKey)}/cloud`),
  });
  const [kind, setKind] = useState("all");

  if (cloud.isLoading) return <Spinner label="Reading AWS and Azure…" />;
  if (cloud.error || !cloud.data) return <ErrorNote error={cloud.error ?? new Error("No data")} />;
  const c = cloud.data;
  if (c.clouds.length === 0) {
    return <Empty>No cloud is connected. Add AWS or Azure keys to connections.env, then tag resources project={projectKey}.</Empty>;
  }
  const unhealthy = (c.states.warning ?? 0) + (c.states.impaired ?? 0);
  const active = c.resources.filter((r) => r.health !== "stopped" && r.health !== "unknown").length;
  const kinds = ["all", ...Object.keys(c.counts).sort()];
  const shown = c.resources.filter((r) => kind === "all" || r.kind === kind);
  const maxCost = Math.max(1, ...(c.cost?.by_service.map((s) => s.amount) ?? [1]));
  const critical = (c.findings_by_severity.critical ?? 0) + (c.findings_by_severity.high ?? 0);

  return (
    <>
      {c.warnings.map((w) => <div key={w} className="notice small">Unavailable: {w}</div>)}
      <div className="grid grid-4">
        <Kpi icon="dollar" label="Cloud cost this month" value={money(c.cost?.month_to_date, c.cost?.currency)}
             foot={c.cost ? `Forecast ${money(c.cost.forecast_month_end, c.cost.currency)} by month end` : "Cost data not enabled"} />
        <Kpi icon="cloud" label="Resources" value={c.resources.length}
             foot={`${active} active · ${unhealthy} need attention`} tone={unhealthy ? "warn" : undefined} />
        <Kpi icon="database" label="Storage used" value={gb(c.storage_gb)}
             foot={Object.entries(c.storage_by_kind).map(([k, v]) => `${k} ${gb(v)}`).join(" · ") || "—"} />
        <Kpi icon="alert" label="Alerts firing" value={c.alerts.length} tone={c.alerts.length ? "bad" : "good"}
             foot={c.findings === null ? "Security findings: admins and auditors" : `${critical} critical/high security findings`} />
      </div>

      <div className="grid grid-2" style={{ alignItems: "start" }}>
        <Card title="Where it runs" actions={<span className="row">{c.clouds.map((x) => <Badge key={x} value="info" label={CLOUD_LABEL[x] ?? x} />)}</span>}>
          <ul className="list small">
            {c.placements.map((p) => (
              <li key={`${p.region}-${p.environment}`}>
                <span className="grow"><strong>{p.region}</strong> <span className="muted">· {p.environment ?? "no environment tag"}</span></span>
                <span className="num muted">{p.resources} resource{p.resources === 1 ? "" : "s"}</span>
              </li>
            ))}
          </ul>
          {Object.keys(c.owners).length > 0 && (
            <p className="xs muted" style={{ margin: "12px 0 0" }}>
              Owners: {Object.entries(c.owners).map(([o, n]) => `${o} (${n})`).join(", ")}
            </p>
          )}
        </Card>
        <Card title="Cost by service">
          {!c.cost ? (
            <Empty>Cost data is off (AWS Cost Explorer charges per request; set AWS_COST_EXPLORER_ENABLED=true to turn it on).</Empty>
          ) : (
            <div className="stack" style={{ gap: 8 }}>
              {c.cost.by_service.map((s) => (
                <div key={s.service} className="cost-row">
                  <span className="small">{s.service}</span>
                  <span className="loss-bar"><span style={{ width: `${(s.amount / maxCost) * 100}%`, background: "var(--accent)" }} /></span>
                  <span className="small num">{money(s.amount, c.cost!.currency)}</span>
                </div>
              ))}
              <p className="xs faint" style={{ margin: 0 }}>
                By cloud: {Object.entries(c.cost.by_cloud).map(([k, v]) => `${CLOUD_LABEL[k] ?? k} ${money(v, c.cost!.currency)}`).join(" · ")}
              </p>
            </div>
          )}
        </Card>
      </div>

      <Card title="Resources" actions={
        <div className="segmented" role="group" aria-label="Resource kind">
          {kinds.map((k) => (
            <button key={k} type="button" aria-pressed={kind === k} onClick={() => setKind(k)}>
              {k === "all" ? `All (${c.resources.length})` : `${k} (${c.counts[k]})`}
            </button>
          ))}
        </div>
      }>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Name and purpose</th><th>Kind</th><th>Where</th><th>Size</th><th>Health</th><th className="num">Storage</th><th>Owner</th><th>Created</th></tr>
            </thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.id}>
                  <td>
                    <div><strong>{r.name}</strong> <span className="xs faint">{CLOUD_LABEL[r.source] ?? r.source}</span></div>
                    <div className="xs muted">{r.purpose ?? "No purpose tag"}</div>
                  </td>
                  <td>{r.kind}</td>
                  <td>{r.region ?? "—"}<div className="xs muted">{r.environment ?? ""}</div></td>
                  <td className="xs">{r.size ?? "—"}</td>
                  <td>
                    <Badge value={HEALTH_TONE[r.health] ?? "muted"} label={r.health === "ok" ? "Healthy" : r.health} />
                    {r.health_detail && <div className="xs muted" style={{ marginTop: 2 }}>{r.health_detail}</div>}
                  </td>
                  <td className="num">{r.storage_gb ? gb(r.storage_gb) : "—"}</td>
                  <td className="xs">{r.owner ?? "—"}</td>
                  <td className="xs">{formatDay(r.created)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid grid-2" style={{ alignItems: "start" }}>
        <Card title="Alerts firing">
          {c.alerts.length === 0 ? <Empty>No alarms firing.</Empty> : (
            <ul className="list small">
              {c.alerts.map((a) => (
                <li key={a.id} style={{ alignItems: "flex-start" }}>
                  <Badge value={SEVERITY_TONE[a.severity ?? "medium"] ?? "warn"} label={a.severity ?? a.state} />
                  <div className="grow">
                    <div>{a.title}</div>
                    <div className="xs muted">{CLOUD_LABEL[a.source] ?? a.source} · {a.state} · since {formatDate(a.started)}</div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Security findings">
          {c.findings === null ? <Empty>Security findings are visible to admins and auditors.</Empty>
            : c.findings.length === 0 ? <Empty>No active findings.</Empty> : (
            <ul className="list small">
              {c.findings.map((f) => (
                <li key={f.id} style={{ alignItems: "flex-start" }}>
                  <Badge value={SEVERITY_TONE[f.severity] ?? "muted"} label={f.severity} />
                  <div className="grow">
                    <div>{f.title}</div>
                    {f.recommendation && <div className="xs muted">Fix: {f.recommendation}</div>}
                    <div className="xs faint">{CLOUD_LABEL[f.source] ?? f.source} · {f.status}</div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}
