import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Project, ProjectHealth, Report } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Badge, Card, Empty, ErrorNote, Spinner } from "../components/ui";

export function ProjectsPage() {
  const { can } = useAuth();
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => api<Project[]>("/projects") });
  return (
    <>
      <div className="page-header">
        <div>
          <h1>Projects</h1>
          <p>Configured projects. Open one to see risk, forecast and team capacity.</p>
        </div>
      </div>
      {projects.isLoading && <Spinner />}
      <ErrorNote error={projects.error} />
      {projects.data?.length === 0 && (
        <Empty>No projects configured yet. Use PUT /api/v1/projects/&lt;KEY&gt; to add one.</Empty>
      )}
      <div className="grid grid-3">
        {projects.data?.map((p) => (
          <Card key={p.key} title={`${p.name}`} actions={<Badge value={p.is_active ? "live" : "disabled"} label={p.key} />}>
            <div className="small muted">{p.github_repos.join(", ") || "No repositories"}</div>
            <div className="small muted">{p.members.length} team members · sprint {p.active_sprint_id ?? "—"}</div>
            {can("analytics:read") && (
              <p>
                <Link to={`/projects/${p.key}`}>View health →</Link>
              </p>
            )}
          </Card>
        ))}
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
      <div className="page-header">
        <div>
          <h1>{h.name}</h1>
          <p>
            {h.project} · as of {h.as_of}
          </p>
        </div>
        <div className="row">
          <button className="button secondary" onClick={() => void health.refetch()} disabled={health.isFetching}>
            Refresh
          </button>
          {can("reports:generate") && (
            <button className="button" onClick={() => generate.mutate()} disabled={generate.isPending}>
              {generate.isPending ? "Generating…" : "Generate weekly report"}
            </button>
          )}
        </div>
      </div>
      <ErrorNote error={generate.error} />
      {h.warnings.map((w) => (
        <div key={w} className="notice small">
          {w}
        </div>
      ))}

      <div className="grid grid-3">
        <Card title="Risk">
          <div className="row">
            <span className="stat">{h.risk.score}</span>
            <Badge value={h.risk.level} />
          </div>
          <div className={`meter ${tone(h.risk.score, 30, 60)}`} aria-label={`Risk ${h.risk.score} of 100`}>
            <span style={{ width: `${h.risk.score}%` }} />
          </div>
        </Card>
        <Card title="Sprint forecast">
          {f ? (
            <>
              <div className="row">
                <span className="stat">{Math.round(f.completion_ratio * 100)}%</span>
                <Badge value={f.status} />
              </div>
              <div className="small muted">
                {f.done_points}/{f.committed_points} points done · projected {f.projected_points} by {f.end} ·{" "}
                {f.working_days_left} working days left
              </div>
              <div className={`meter ${f.status === "on_track" ? "good" : f.status === "at_risk" ? "warn" : "bad"}`}>
                <span style={{ width: `${Math.min(100, (f.done_points / (f.committed_points || 1)) * 100)}%` }} />
              </div>
            </>
          ) : (
            <Empty>No active sprint configured.</Empty>
          )}
        </Card>
        <Card title="Issues">
          <div className="row">
            {Object.entries(h.issue_counts).map(([category, count]) => (
              <div key={category}>
                <div className="stat">{count}</div>
                <div className="small muted">{category.replace("_", " ")}</div>
              </div>
            ))}
          </div>
        </Card>
      </div>

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

      <div className="grid grid-2">
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
      </div>
    </>
  );
}
