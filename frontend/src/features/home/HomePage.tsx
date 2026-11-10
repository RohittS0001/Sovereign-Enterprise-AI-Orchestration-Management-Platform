import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../../shared/api/client";
import type { AgentRun, Approval, Page } from "../../shared/api/types";
import { useAuth } from "../../shared/auth/AuthContext";
import { Badge, Card, Empty, ErrorNote, Kpi, PageHeader, Ring, Spinner, formatDate, formatDay, toneFor } from "../../shared/ui/components";
import { Icon } from "../../shared/ui/icons";
import { BriefingCard } from "../projects/BriefingCard";

interface PortfolioBrief {
  as_of: string;
  projects: {
    key: string;
    name: string;
    risk_score: number;
    risk_level: string;
    top_risks: string[];
    chance_by_target: number | null;
    target_date: string | null;
    likely_finish: string | null;
    decision_needed: string | null;
  }[];
  decisions: string[];
}

function greeting(): string {
  const hour = new Date().getHours();
  return hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

/** Portfolio at a glance: KPIs → this week's decisions → project risk → recent assistant work. */
export function HomePage() {
  const { user, can } = useAuth();
  const brief = useQuery({ queryKey: ["briefing"], queryFn: () => api<PortfolioBrief>("/briefing") });
  const approvals = useQuery({
    queryKey: ["approvals", "pending"],
    queryFn: () => api<Page<Approval>>("/approvals?status=pending"),
  });
  const runs = useQuery({
    queryKey: ["runs", "recent"],
    queryFn: () => api<Page<AgentRun>>("/agent/runs?limit=5"),
    enabled: can("agent:query"),
  });
  const projects = brief.data?.projects ?? [];
  const highRisk = projects.filter((p) => p.risk_score >= 60).length;
  const avgRisk = projects.length ? Math.round(projects.reduce((s, p) => s + p.risk_score, 0) / projects.length) : 0;

  return (
    <>
      <PageHeader
        eyebrow={new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })}
        title={`${greeting()}, ${user?.full_name.split(" ")[0] ?? "there"}`}
        description="Here's where your portfolio stands and what needs a decision."
        actions={
          can("agent:query") && (
            <Link className="button" to="/assistant">
              <Icon name="sparkles" /> Ask the assistant
            </Link>
          )
        }
      />
      <ErrorNote error={brief.error} />
      <div className="grid grid-4">
        <Kpi icon="folder" label="Active projects" value={brief.isLoading ? "…" : projects.length} foot="Configured for analytics" />
        <Kpi icon="alert" label="High-risk projects" value={brief.isLoading ? "…" : highRisk}
             tone={highRisk ? "bad" : "good"} foot={`Average risk ${avgRisk}/100`} />
        <Kpi icon="clock" label="Decisions this week" value={brief.isLoading ? "…" : brief.data?.decisions.length ?? 0}
             tone={brief.data?.decisions.length ? "warn" : "good"} foot="From the chief-of-staff brief" />
        <Kpi icon="check" label="Pending approvals" value={approvals.data?.total ?? "…"}
             tone={approvals.data?.total ? "warn" : undefined}
             foot={<Link to="/approvals">Review queue →</Link>} />
      </div>

      <div className="split">
        <div>
          <Card title="Portfolio risk" actions={<Link className="small" to="/projects">All projects →</Link>}>
            {brief.isLoading && <Spinner label="Analysing projects…" />}
            {brief.data && projects.length === 0 && <Empty>No projects configured yet.</Empty>}
            <ul className="list">
              {projects.map((p) => (
                <li key={p.key}>
                  <Ring value={p.risk_score} size={52} tone={toneFor(p.risk_score, 30, 60)} />
                  <div className="grow">
                    <div className="row">
                      <Link to={`/projects/${p.key}`}><strong>{p.name}</strong></Link>
                      <Badge value={p.risk_level} label={`${p.risk_level} risk`} />
                    </div>
                    <div className="small muted truncate">{p.top_risks[0] ?? "No active risk factors"}</div>
                  </div>
                  <div style={{ textAlign: "right" }}>
                    <div className="num" style={{ fontWeight: 600 }}>
                      {p.chance_by_target === null ? "—" : `${Math.round(p.chance_by_target * 100)}%`}
                    </div>
                    <div className="xs faint">by {formatDay(p.target_date)} · likely {formatDay(p.likely_finish)}</div>
                  </div>
                </li>
              ))}
            </ul>
          </Card>
          {can("agent:query") && (
            <Card title="Recent assistant runs" actions={<Link className="small" to="/assistant">Open assistant →</Link>}>
              {runs.data?.items.length === 0 && <Empty>No questions asked yet.</Empty>}
              <ul className="list">
                {runs.data?.items.map((r) => (
                  <li key={r.run_id}>
                    <Icon name="bot" width={18} height={18} className="muted" />
                    <div className="grow">
                      <div className="truncate">{r.query}</div>
                      <div className="xs faint">{formatDate(r.started_at)} · {r.steps.length} tool calls</div>
                    </div>
                    <Badge value={r.status} />
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
        <div>{projects.length > 0 && <BriefingCard />}</div>
      </div>
    </>
  );
}
