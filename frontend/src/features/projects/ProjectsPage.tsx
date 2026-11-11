import { BriefingCard } from "./BriefingCard";
import { BacklogPlanner, DelayPanel, DoraPanel, ReleasePanel, RoadmapPanel } from "./DeliveryPanels";
import { CloudPanel } from "../cloud/CloudPanel";
import { DeliveryTwin } from "./DeliveryTwin";
import { CommitmentsCard, FinanceCard } from "./FinanceCard";
import { ProductionCard } from "./ProductionCard";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../../shared/api/client";
import type { Project, ProjectHealth, Report } from "../../shared/api/types";
import { useAuth } from "../../shared/auth/AuthContext";
import { Badge, Card, Empty, ErrorNote, PageHeader, Ring, Spinner, Tabs, formatDay, toneFor } from "../../shared/ui/components";
import { Icon } from "../../shared/ui/icons";

export function ProjectsPage() {
  const { can } = useAuth();
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => api<Project[]>("/projects") });
  const brief = useQuery({
    queryKey: ["briefing"],
    queryFn: () => api<{ projects: { key: string; risk_score: number; risk_level: string; chance_by_target: number | null }[] }>("/briefing"),
    enabled: can("analytics:read"),
  });
  const risk = new Map((brief.data?.projects ?? []).map((p) => [p.key, p]));
  return (
    <>
      <PageHeader eyebrow="Delivery" title="Projects" description="Configured projects. Open one to see risk, forecast and team capacity." />
      {can("analytics:read") && projects.data && projects.data.length > 0 && <BriefingCard />}
      {projects.isLoading && <Spinner />}
      <ErrorNote error={projects.error} />
      {projects.data?.length === 0 && (
        <Empty>No projects configured yet. Use PUT /api/v1/projects/&lt;KEY&gt; to add one.</Empty>
      )}
      <div className="grid grid-3">
        {projects.data?.map((p) => {
          const r = risk.get(p.key);
          return (
            <Card key={p.key} className="interactive" title={p.name} actions={<Badge value={p.is_active ? "live" : "disabled"} label={p.key} />}>
              <div className="row" style={{ gap: 16, alignItems: "center" }}>
                {r && <Ring value={r.risk_score} size={56} tone={toneFor(r.risk_score, 30, 60)} label="risk" />}
                <div className="stack" style={{ gap: 2 }}>
                  <span className="small muted">{p.github_repos.join(", ") || "No repositories"}</span>
                  <span className="small muted">{p.members.length} team members · sprint {p.active_sprint_id ?? "—"}</span>
                  {r?.chance_by_target != null && (
                    <span className="small"><strong className="num">{Math.round(r.chance_by_target * 100)}%</strong> chance to hit the sprint</span>
                  )}
                </div>
              </div>
              {can("analytics:read") && (
                <p style={{ margin: "12px 0 0" }}>
                  <Link to={`/projects/${p.key}`}>View health →</Link>
                </p>
              )}
            </Card>
          );
        })}
      </div>
    </>
  );
}

function tone(value: number, warnAt: number, badAt: number): string {
  return value >= badAt ? "bad" : value >= warnAt ? "warn" : "good";
}

export function ProjectHealthPage() {
  const { key = "" } = useParams();
  const navigate = useNavigate();
  const { can } = useAuth();
  const health = useQuery({
    queryKey: ["health", key],
    queryFn: () => api<ProjectHealth>(`/projects/${encodeURIComponent(key)}/health`),
  });
  const [tab, setTab] = useState<"overview" | "delay" | "cloud" | "forecast" | "roadmap" | "dora" | "releases" | "planner" | "team" | "business">("overview");
  const generate = useMutation({
    mutationFn: () => api<Report>("/reports/generate", { body: { project_key: key } }),
    onSuccess: (report) => navigate(`/reports?open=${report.id}`),
  });

  if (health.isLoading) return <Spinner label="Analysing project…" />;
  if (health.error || !health.data) return <ErrorNote error={health.error ?? new Error("No data")} />;
  const h = health.data;
  const f = h.forecast;

  return (
    <>
      <PageHeader
        eyebrow={`Project · ${h.project}`}
        title={h.name}
        description={`Health as of ${formatDay(h.as_of)} — risk, forecast, team and production in one place.`}
        actions={
          <>
            <button className="button secondary" onClick={() => void health.refetch()} disabled={health.isFetching}>
              <Icon name="refresh" /> Refresh
            </button>
            {can("reports:generate") && (
              <button className="button" onClick={() => generate.mutate()} disabled={generate.isPending}>
                <Icon name="report" /> {generate.isPending ? "Generating…" : "Generate weekly report"}
              </button>
            )}
          </>
        }
      />
      <ErrorNote error={generate.error} />
      {h.warnings.map((w) => (
        <div key={w} className="notice small">
          {w}
        </div>
      ))}

      <div className="card" style={{ display: "flex", gap: 24, alignItems: "center", flexWrap: "wrap" }}>
        <Ring value={h.risk.score} label="risk" size={96} tone={toneFor(h.risk.score, 30, 60)} />
        <div style={{ flex: 1, minWidth: 220 }}>
          <div className="row">
            <h2 style={{ fontSize: "1.125rem" }}>{h.risk.level === "high" ? "High risk" : h.risk.level === "medium" ? "Medium risk" : "Low risk"}</h2>
            <Badge value={h.risk.level} />
          </div>
          <p className="small muted" style={{ margin: "4px 0 0" }}>
            {h.risk.factors.filter((x) => x.score > 0).sort((a, b) => b.score / b.max_score - a.score / a.max_score)[0]?.recommendation ?? "No action needed right now."}
          </p>
        </div>
        <div className="stack" style={{ flex: "1 1 240px", gap: 6 }}>
          {f ? (
            <>
              <div className="row between small">
                <span className="muted">Sprint forecast</span>
                <Badge value={f.status} />
              </div>
              <div className="row between">
                <span className="stat">{Math.round(f.completion_ratio * 100)}%</span>
                <span className="xs faint num">{f.done_points}/{f.committed_points} pts · {f.working_days_left} days left</span>
              </div>
              <div className={`meter ${f.status === "on_track" ? "good" : f.status === "at_risk" ? "warn" : "bad"}`}>
                <span style={{ width: `${Math.min(100, (f.done_points / (f.committed_points || 1)) * 100)}%` }} />
              </div>
            </>
          ) : (
            <span className="small muted">No active sprint configured.</span>
          )}
          <div className="row xs muted">
            {Object.entries(h.issue_counts).map(([category, count]) => (
              <span key={category}><strong className="num">{count}</strong> {category.replace("_", " ")}</span>
            ))}
          </div>
        </div>
      </div>

      <Tabs label="Project sections" value={tab} onChange={setTab} tabs={[
        { id: "overview", label: "Overview" },
        ...(can("analytics:read") ? [
          { id: "delay" as const, label: "Why is it late?" },
          { id: "cloud" as const, label: "Cloud & infra" },
          { id: "forecast" as const, label: "Forecast & what-ifs" },
          { id: "roadmap" as const, label: "Roadmap" },
          { id: "dora" as const, label: "DORA metrics" },
          { id: "releases" as const, label: "Releases & retro" },
        ] : []),
        ...(can("agent:query") && can("analytics:read") ? [{ id: "planner" as const, label: "Backlog planner" }] : []),
        { id: "team", label: "Team" },
        ...(h.production || h.finance || h.commitments ? [{ id: "business" as const, label: "Production & finance" }] : []),
      ]} />

      {tab === "overview" && (
        <>
      <Card title="Risk factors">
        <div className="stack">
          {h.risk.factors.map((factor) => (
            <div key={factor.name}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <strong>{factor.name}</strong>
                <span className="small muted">
                  {factor.score}/{factor.max_score}
                </span>
              </div>
              <div className={`meter ${tone(factor.score / factor.max_score, 0.3, 0.6)}`}>
                <span style={{ width: `${(factor.score / factor.max_score) * 100}%` }} />
              </div>
              {factor.evidence.length > 0 && (
                <ul className="small">
                  {factor.evidence.map((e) => (
                    <li key={e}>{e}</li>
                  ))}
                </ul>
              )}
              {factor.recommendation && factor.score > 0 && (
                <div className="small muted">→ {factor.recommendation}</div>
              )}
            </div>
          ))}
        </div>
      </Card>

        <Card title="Jira ↔ GitHub checks">
          {h.contradictions.length === 0 ? (
            <Empty>Jira and GitHub agree.</Empty>
          ) : (
            <ul className="small">
              {h.contradictions.map((c) => (
                <li key={`${c.kind}-${c.issue}`}>
                  <Badge value="warn" label={c.kind.replace(/_/g, " ")} /> {c.detail}
                </li>
              ))}
            </ul>
          )}
        </Card>
        </>
      )}
      {tab === "forecast" && <DeliveryTwin projectKey={h.project} />}
      {tab === "delay" && <DelayPanel projectKey={h.project} />}
      {tab === "cloud" && <CloudPanel projectKey={h.project} />}
      {tab === "roadmap" && <RoadmapPanel projectKey={h.project} />}
      {tab === "dora" && <DoraPanel projectKey={h.project} />}
      {tab === "releases" && <ReleasePanel projectKey={h.project} />}
      {tab === "planner" && <BacklogPlanner projectKey={h.project} />}
      {tab === "team" && (
        <Card title="Team capacity">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Member</th>
                  <th>Load</th>
                  <th>Leave</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {h.workload.members.map((m) => (
                  <tr key={m.name}>
                    <td>
                      {m.name}
                      {m.at_risk_issues.length > 0 && (
                        <div className="small" style={{ color: "var(--bad)" }}>
                          At risk: {m.at_risk_issues.join(", ")}
                        </div>
                      )}
                    </td>
                    <td>
                      {m.open_points} / {m.effective_capacity} pts
                    </td>
                    <td>{m.leave_days ? `${m.leave_days} days` : "—"}</td>
                    <td>
                      <Badge value={m.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {h.workload.suggestions.length > 0 && (
            <>
              <h3>Suggested reassignments</h3>
              <ul className="small">
                {h.workload.suggestions.map((s) => (
                  <li key={s.issue}>
                    Move <strong>{s.issue}</strong> ({s.points} pts) from {s.from_member} to {s.to_member} — {s.reason}
                  </li>
                ))}
              </ul>
            </>
          )}
        </Card>
      )}
      {tab === "business" && (
        <div className="grid grid-2">
          {h.production && <ProductionCard production={h.production} />}
          {h.finance && <FinanceCard finance={h.finance} />}
          {h.commitments && <CommitmentsCard deals={h.commitments} />}
        </div>
      )}
    </>
  );
}
