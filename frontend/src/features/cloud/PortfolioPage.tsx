import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../../shared/api/client";
import { Avatar, Badge, Card, Empty, ErrorNote, Kpi, PageHeader, Spinner } from "../../shared/ui/components";
import { gb, money } from "./CloudPanel";

interface PortfolioRow {
  project: string;
  name: string;
  team: string[];
  resources: number;
  active: number;
  unhealthy: number;
  storage_gb: number;
  cost_month_to_date: number | null;
  cost_forecast: number | null;
  currency: string | null;
  alerts: number;
  critical_findings: number | null;
  regions: string[];
  warnings: string[];
}

interface Portfolio {
  as_of: string;
  projects: PortfolioRow[];
  totals: Record<string, number>;
  people: Record<string, string[]>;
}

export function PortfolioPage() {
  const portfolio = useQuery({ queryKey: ["portfolio"], queryFn: () => api<Portfolio>("/portfolio") });
  const p = portfolio.data;
  const currency = p?.projects.find((r) => r.currency)?.currency ?? "USD";

  return (
    <>
      <PageHeader eyebrow="Delivery" title="Portfolio"
                  description="Every project side by side: who works on it, what it runs in the cloud, its health, storage and cost." />
      {portfolio.isLoading && <Spinner label="Collecting every project…" />}
      <ErrorNote error={portfolio.error} />
      {p && (
        <>
          <div className="grid grid-4">
            <Kpi icon="folder" label="Active projects" value={p.totals.projects} foot={`${Object.keys(p.people).length} people`} />
            <Kpi icon="dollar" label="Cloud cost this month" value={money(p.totals.cost_month_to_date, currency)}
                 foot={`Forecast ${money(p.totals.cost_forecast, currency)}`} />
            <Kpi icon="cloud" label="Cloud resources" value={p.totals.resources}
                 foot={`${p.totals.unhealthy} need attention`} tone={p.totals.unhealthy ? "warn" : undefined} />
            <Kpi icon="database" label="Storage used" value={gb(p.totals.storage_gb)} foot={`${p.totals.alerts} alerts firing`} />
          </div>

          <Card title="Projects">
            {p.projects.length === 0 ? <Empty>No active projects.</Empty> : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Project</th><th>Team</th><th className="num">Resources</th><th className="num">Storage</th>
                      <th className="num">Cost this month</th><th className="num">Forecast</th><th>Alerts</th><th>Security</th><th>Regions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {p.projects.map((r) => (
                      <tr key={r.project}>
                        <td><Link to={`/projects/${r.project}`}><strong>{r.name}</strong></Link><div className="xs muted">{r.project}</div></td>
                        <td>
                          <div className="avatar-stack" title={r.team.join(", ")}>
                            {r.team.slice(0, 4).map((name) => <Avatar key={name} name={name} />)}
                          </div>
                          <div className="xs muted">{r.team.length} people</div>
                        </td>
                        <td className="num">{r.resources}<div className="xs muted">{r.unhealthy ? `${r.unhealthy} unhealthy` : "all healthy"}</div></td>
                        <td className="num">{gb(r.storage_gb)}</td>
                        <td className="num">{money(r.cost_month_to_date, r.currency ?? currency)}</td>
                        <td className="num">{money(r.cost_forecast, r.currency ?? currency)}</td>
                        <td>{r.alerts ? <Badge value="bad" label={`${r.alerts} firing`} /> : <Badge value="good" label="None" />}</td>
                        <td>
                          {r.critical_findings === null ? <span className="xs faint">Admins only</span>
                            : r.critical_findings ? <Badge value="bad" label={`${r.critical_findings} critical`} /> : <Badge value="good" label="No critical" />}
                        </td>
                        <td className="xs">{r.regions.join(", ") || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card title="Who works on what">
            <ul className="list small">
              {Object.entries(p.people).map(([person, projects]) => (
                <li key={person}>
                  <Avatar name={person} />
                  <span className="grow">{person}</span>
                  <span className="row">{projects.map((key) => <Link key={key} to={`/projects/${key}`}><Badge value="info" label={key} /></Link>)}</span>
                </li>
              ))}
            </ul>
          </Card>
        </>
      )}
    </>
  );
}
