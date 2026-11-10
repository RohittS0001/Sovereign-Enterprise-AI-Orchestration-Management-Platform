import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../shared/api/client";
import { Badge, Card, Empty, ErrorNote, Kpi, Markdown, Spinner, formatDay } from "../../shared/ui/components";
import { Icon } from "../../shared/ui/icons";

// ------------------------------------------------------------------ types

interface RoadmapItem {
  key: string;
  summary: string;
  assignee: string | null;
  status: string;
  points: number;
  duration_days: number;
  start: string;
  finish: string;
  start_day: number;
  finish_day: number;
  slack_days: number;
  critical: boolean;
  depends_on: string[];
  after: string[];
  due: string | null;
  late_by_days: number | null;
}

interface Roadmap {
  finish: string;
  total_working_days: number;
  critical_path: string[];
  items: RoadmapItem[];
  external_blockers: Record<string, string[]>;
  late_vs_sprint: boolean;
  sprint_end: string | null;
  notes: string[];
}

type Band = "elite" | "high" | "medium" | "low";

interface DoraMetric {
  name: string;
  value: number | null;
  unit: string;
  band: Band | null;
  evidence: string[];
  source: string | null;
  note: string | null;
}

interface DoraReport {
  window_days: number;
  deployment_frequency: DoraMetric;
  lead_time: DoraMetric;
  change_failure_rate: DoraMetric;
  time_to_restore: DoraMetric;
  overall: Band | null;
  ci_failure_rate: number | null;
  recommendations: string[];
}

interface ReleaseNotes {
  since: string;
  until: string;
  markdown: string;
  merged_pulls: number;
  completed_issues: number;
}

interface Retrospective {
  sprint: string | null;
  committed_points: number;
  done_points: number;
  completion_ratio: number | null;
  carry_over: string[];
  went_well: string[];
  to_improve: string[];
  actions: string[];
  narrative: string;
  written_by: string;
}

interface StoryDraft {
  title: string;
  description: string;
  acceptance_criteria: string[];
  points: number | null;
  estimate_basis: string | null;
  similar_issues: string[];
}

interface EpicDraft {
  title: string;
  description: string;
  stories: StoryDraft[];
}

interface BacklogProposal {
  epics: EpicDraft[];
  assumptions: string[];
  open_questions: string[];
  drafted_by: string;
  total_points: number;
  injection_warning: string[];
}

interface SubmitResult {
  tool: string;
  title: string;
  status: string;
  approval_id: string | null;
  error: string | null;
}

/** Working days for display: whole days, "< 1 day" below one. */
const days = (value: number) => (value < 1 ? "< 1 day" : `${Math.round(value)} ${Math.round(value) === 1 ? "day" : "days"}`);
const base = (key: string) => `/projects/${encodeURIComponent(key)}`;
const BAND_TONE: Record<Band, string> = { elite: "good", high: "good", medium: "warn", low: "bad" };

function BandBadge({ band }: { band: Band | null }) {
  return band ? <Badge value={BAND_TONE[band]} label={band} /> : <Badge value="unknown" label="no data" />;
}

// ------------------------------------------------------------------ roadmap

export function RoadmapPanel({ projectKey }: { projectKey: string }) {
  const roadmap = useQuery({
    queryKey: ["roadmap", projectKey],
    queryFn: () => api<Roadmap>(`${base(projectKey)}/roadmap`),
  });
  if (roadmap.isLoading) return <Spinner label="Scheduling open work…" />;
  if (roadmap.error || !roadmap.data) return <ErrorNote error={roadmap.error ?? new Error("No data")} />;
  const r = roadmap.data;
  const span = Math.max(r.total_working_days, 1);

  return (
    <>
      <div className="grid grid-3">
        <Kpi icon="calendar" label="Projected finish" value={formatDay(r.finish)}
             foot={r.sprint_end ? `Sprint ends ${formatDay(r.sprint_end)}` : "No sprint end date"}
             tone={r.late_vs_sprint ? "bad" : "good"} />
        <Kpi icon="clock" label="Work left" value={days(r.total_working_days)} foot="Working days, team in parallel" />
        <Kpi icon="activity" label="Critical path" value={r.critical_path.length}
             foot={r.critical_path.join(" → ") || "Nothing open"} />
      </div>
      {r.late_vs_sprint && (
        <div className="notice small">
          The critical path finishes after the sprint ends. Shorten it: unblock or reassign {r.critical_path[0]} first.
        </div>
      )}
      <Card title="Schedule" actions={
        <div className="legend" aria-hidden="true">
          <span><i className="critical" />Critical</span>
          <span><i className="accent" />Has slack</span>
        </div>
      }>
        {r.items.length === 0 ? (
          <Empty>No open issues to schedule.</Empty>
        ) : (
          <ol className="gantt" aria-label="Roadmap schedule">
            {r.items.map((item) => (
              <li key={item.key} className="gantt-row">
                <div className="gantt-label">
                  <strong className="num">{item.key}</strong>
                  <span className="truncate small muted" title={item.summary}>{item.summary}</span>
                </div>
                <div className="gantt-track">
                  <span
                    className={`gantt-bar ${item.critical ? "critical" : ""}`}
                    style={{ left: `${(item.start_day / span) * 100}%`, width: `${Math.max(2, ((item.finish_day - item.start_day) / span) * 100)}%` }}
                    title={`${item.key}: ${formatDay(item.start)} → ${formatDay(item.finish)}`}
                  />
                </div>
                <div className="gantt-meta xs">
                  <span className="num">{formatDay(item.start)} → {formatDay(item.finish)}</span>
                  <span className="muted">
                    {item.assignee ?? "Unassigned"}
                    {item.depends_on.length > 0 && ` · after ${item.depends_on.join(", ")}`}
                  </span>
                  {item.late_by_days != null && <Badge value="bad" label={`${item.late_by_days}d past due`} />}
                  {!item.critical && item.slack_days >= 0.5 && <span className="faint">{days(item.slack_days)} slack</span>}
                </div>
              </li>
            ))}
          </ol>
        )}
      </Card>
      {(r.notes.length > 0 || Object.keys(r.external_blockers).length > 0) && (
        <Card title="Assumptions">
          <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>
            {r.notes.map((n) => <li key={n}>{n}</li>)}
            {Object.entries(r.external_blockers).map(([key, blockers]) => (
              <li key={key}>{key} waits on {blockers.join(", ")} (outside this project).</li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}

// ------------------------------------------------------------------ DORA

function formatMetric(m: DoraMetric): string {
  if (m.value === null) return "—";
  if (m.unit === "%") return `${m.value}%`;
  if (m.unit === "days") return m.value < 1 ? "< 1 day" : `${m.value} ${m.value === 1 ? "day" : "days"}`;
  return `${m.value} / week`;
}

export function DoraPanel({ projectKey }: { projectKey: string }) {
  const [windowDays, setWindowDays] = useState(30);
  const dora = useQuery({
    queryKey: ["dora", projectKey, windowDays],
    queryFn: () => api<DoraReport>(`${base(projectKey)}/dora?window_days=${windowDays}`),
  });
  const d = dora.data;
  const metrics = d ? [d.deployment_frequency, d.lead_time, d.change_failure_rate, d.time_to_restore] : [];

  return (
    <>
      <div className="row between" style={{ marginBottom: 16 }}>
        <div className="row small muted">
          Overall performance {d ? <BandBadge band={d.overall} /> : null}
        </div>
        <div className="segmented" role="group" aria-label="Time window">
          {[30, 90].map((n) => (
            <button key={n} type="button" aria-pressed={windowDays === n} onClick={() => setWindowDays(n)}>
              Last {n} days
            </button>
          ))}
        </div>
      </div>
      {dora.isLoading && <Spinner label="Calculating DORA metrics…" />}
      <ErrorNote error={dora.error} />
      {d && (
        <>
          <div className="grid grid-4">
            {metrics.map((m) => (
              <section key={m.name} className="card kpi">
                <div className="kpi-head">
                  <span className="kpi-label">{m.name}</span>
                  <BandBadge band={m.band} />
                </div>
                <div className="kpi-value">{formatMetric(m)}</div>
                <div className="kpi-foot">{m.note ?? m.source ?? ""}</div>
              </section>
            ))}
          </div>
          <div className="grid grid-2">
            <Card title="Evidence">
              {metrics.every((m) => m.evidence.length === 0) ? (
                <Empty>No deployments, merged pull requests or resolved incidents in this window.</Empty>
              ) : (
                <div className="stack">
                  {metrics.filter((m) => m.evidence.length > 0).map((m) => (
                    <div key={m.name}>
                      <div className="small"><strong>{m.name}</strong></div>
                      <ul className="xs muted" style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                        {m.evidence.slice(0, 5).map((e) => <li key={e}>{e}</li>)}
                      </ul>
                    </div>
                  ))}
                </div>
              )}
            </Card>
            <Card title="What to improve">
              {d.ci_failure_rate !== null && (
                <p className="small" style={{ marginTop: 0 }}>
                  Default-branch CI failure rate: <strong className="num">{Math.round(d.ci_failure_rate * 100)}%</strong>
                </p>
              )}
              {d.recommendations.length === 0 ? (
                <Empty>Nothing stands out. Keep the current cadence.</Empty>
              ) : (
                <ul className="list small">
                  {d.recommendations.map((r) => (
                    <li key={r}><Icon name="trend" /> <span className="grow">{r}</span></li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        </>
      )}
    </>
  );
}

// ------------------------------------------------------------------ releases & retro

export function ReleasePanel({ projectKey }: { projectKey: string }) {
  const notes = useQuery({
    queryKey: ["release-notes", projectKey],
    queryFn: () => api<ReleaseNotes>(`${base(projectKey)}/release-notes`),
  });
  const retro = useQuery({
    queryKey: ["retro", projectKey],
    queryFn: () => api<Retrospective>(`${base(projectKey)}/retrospective`),
  });
  const [copied, setCopied] = useState(false);

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="grid grid-2" style={{ alignItems: "start" }}>
      <Card
        title="Release notes"
        actions={notes.data && (
          <button type="button" className="button secondary small" onClick={() => void copy(notes.data!.markdown)}>
            {copied ? "Copied" : "Copy markdown"}
          </button>
        )}
      >
        {notes.isLoading && <Spinner />}
        <ErrorNote error={notes.error} />
        {notes.data && (
          <>
            <p className="xs faint" style={{ marginTop: 0 }}>
              {notes.data.completed_issues} completed issue(s) · {notes.data.merged_pulls} merged pull request(s)
            </p>
            <div className="small compact-md">
              <Markdown text={notes.data.markdown.split("\n").slice(2).join("\n")} />
            </div>
          </>
        )}
      </Card>
      <Card title={`Retrospective${retro.data?.sprint ? ` · ${retro.data.sprint}` : ""}`}
            actions={retro.data && <Badge value="demo" label={retro.data.written_by === "template" ? "From data" : "AI summary"} />}>
        {retro.isLoading && <Spinner label="Collecting sprint facts…" />}
        <ErrorNote error={retro.error} />
        {retro.data && (
          <div className="stack">
            <p className="small" style={{ margin: 0 }}>{retro.data.narrative}</p>
            <RetroList title="Went well" items={retro.data.went_well} tone="good" />
            <RetroList title="To improve" items={retro.data.to_improve} tone="warn" />
            <RetroList title="Actions" items={retro.data.actions} tone="info" />
            {retro.data.carry_over.length > 0 && <RetroList title="Carry over" items={retro.data.carry_over} tone="muted" />}
          </div>
        )}
      </Card>
    </div>
  );
}

function RetroList({ title, items, tone }: { title: string; items: string[]; tone: string }) {
  if (items.length === 0) return null;
  return (
    <div>
      <div className="row small" style={{ marginBottom: 6 }}>
        <Badge value={tone} label={title} />
      </div>
      <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
        {items.map((item) => <li key={item}>{item}</li>)}
      </ul>
    </div>
  );
}

// ------------------------------------------------------------------ backlog planner

const SAMPLE = `# Refund disputes
- Merchants can open a dispute for a refund
  Given a dispute, when it is resolved, then the merchant is notified
- Support agents see a queue of open disputes

## Reporting
- Export the refunds report as CSV
- As a finance lead I want a daily refunds summary e-mail`;

export function BacklogPlanner({ projectKey }: { projectKey: string }) {
  const queryClient = useQueryClient();
  const [requirements, setRequirements] = useState("");
  const [proposal, setProposal] = useState<BacklogProposal | null>(null);
  const draft = useMutation({
    mutationFn: () => api<BacklogProposal>(`${base(projectKey)}/backlog/draft`, { body: { requirements } }),
    onSuccess: (data) => { setProposal(data); submit.reset(); },
  });
  const submit = useMutation({
    mutationFn: (body: BacklogProposal) => api<SubmitResult[]>(`${base(projectKey)}/backlog/submit`, { body }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["approvals"] }),
  });

  const storyCount = proposal?.epics.reduce((n, e) => n + e.stories.length, 0) ?? 0;
  const totalPoints = proposal?.epics.reduce((n, e) => n + e.stories.reduce((m, s) => m + (s.points ?? 0), 0), 0) ?? 0;
  const tooShort = requirements.trim().length < 20;

  function removeStory(epicIndex: number, storyIndex: number) {
    if (!proposal) return;
    const epics = proposal.epics
      .map((e, i) => (i === epicIndex ? { ...e, stories: e.stories.filter((_, j) => j !== storyIndex) } : e))
      .filter((e) => e.stories.length > 0);
    setProposal({ ...proposal, epics });
  }

  function setPoints(epicIndex: number, storyIndex: number, points: number) {
    if (!proposal) return;
    const epics = proposal.epics.map((e, i) => i !== epicIndex ? e : {
      ...e, stories: e.stories.map((s, j) => (j === storyIndex ? { ...s, points } : s)),
    });
    setProposal({ ...proposal, epics });
  }

  return (
    <div className="split">
      <div>
        <Card title="Requirements" actions={
          <button type="button" className="button ghost small" onClick={() => setRequirements(SAMPLE)}>Use an example</button>
        }>
          <form className="stack" onSubmit={(e) => { e.preventDefault(); if (!tooShort) draft.mutate(); }}>
            <label>
              Paste a requirements document or PRD
              <textarea
                value={requirements}
                onChange={(e) => setRequirements(e.target.value)}
                maxLength={20000}
                rows={10}
                placeholder="Headings become epics, bullet points become stories. A connected model writes acceptance criteria."
              />
            </label>
            <div className="row between">
              <span className="xs faint num">{requirements.length.toLocaleString()} / 20,000</span>
              <button type="submit" className="button" disabled={tooShort || draft.isPending}>
                <Icon name="sparkles" /> {draft.isPending ? "Drafting…" : "Draft backlog"}
              </button>
            </div>
          </form>
          <ErrorNote error={draft.error} />
        </Card>

        {proposal && (
          <Card
            title="Proposed backlog"
            actions={<span className="xs muted num">{proposal.epics.length} epic(s) · {storyCount} stories · {totalPoints} pts</span>}
          >
            {proposal.injection_warning.length > 0 && (
              <div className="notice small">The document contains text that looks like instructions to the AI; it was treated as data only.</div>
            )}
            {proposal.epics.length === 0 && <Empty>No stories found. Add headings and bullet points, then draft again.</Empty>}
            <div className="stack">
              {proposal.epics.map((epic, ei) => (
                <section key={`${epic.title}-${ei}`} className="epic">
                  <div className="row between">
                    <h3 style={{ margin: 0 }}>{epic.title}</h3>
                    <Badge value="demo" label="Epic" />
                  </div>
                  {epic.description && <p className="small muted" style={{ margin: "4px 0 0" }}>{epic.description}</p>}
                  <ul className="list" style={{ marginTop: 8 }}>
                    {epic.stories.map((story, si) => (
                      <li key={`${story.title}-${si}`} style={{ alignItems: "flex-start" }}>
                        <div className="grow">
                          <div className="small"><strong>{story.title}</strong></div>
                          {story.acceptance_criteria.length > 0 && (
                            <ul className="xs muted" style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                              {story.acceptance_criteria.map((c) => <li key={c}>{c}</li>)}
                            </ul>
                          )}
                          {story.estimate_basis && <div className="xs faint" style={{ marginTop: 4 }}>{story.estimate_basis}</div>}
                        </div>
                        <label className="points">
                          <span className="sr-only">Story points for {story.title}</span>
                          <select value={story.points ?? 3} onChange={(e) => setPoints(ei, si, Number(e.target.value))}>
                            {[1, 2, 3, 5, 8, 13, 21].map((p) => <option key={p} value={p}>{p} pts</option>)}
                          </select>
                        </label>
                        <button type="button" className="icon-button" aria-label={`Remove ${story.title}`} onClick={() => removeStory(ei, si)}>
                          <Icon name="x" />
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
            </div>
            {storyCount > 0 && (
              <div className="row between" style={{ marginTop: 16 }}>
                <span className="xs muted">Nothing is created until someone approves each item.</span>
                <button type="button" className="button" disabled={submit.isPending || submit.isSuccess}
                        onClick={() => submit.mutate(proposal)}>
                  <Icon name="send" /> {submit.isPending ? "Sending…" : submit.isSuccess ? "Sent for approval" : "Send to Jira for approval"}
                </button>
              </div>
            )}
            <ErrorNote error={submit.error} />
            {submit.data && (
              <div className="notice info small" role="status">
                {submit.data.filter((r) => r.status === "pending_approval").length} of {submit.data.length} item(s) are waiting for approval.{" "}
                <Link to="/approvals">Review approvals</Link>
                {submit.data.some((r) => r.error) && (
                  <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                    {submit.data.filter((r) => r.error).map((r) => <li key={r.title}>{r.title}: {r.error}</li>)}
                  </ul>
                )}
              </div>
            )}
          </Card>
        )}
      </div>

      <Card title="How it works">
        <ol className="small muted" style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 8 }}>
          <li>Headings become epics; bullets and "As a … I want" lines become stories.</li>
          <li>Each story is estimated from the most similar past issues in this project.</li>
          <li>Review: change points or remove stories you don't want.</li>
          <li>Sending creates one approval per epic and story. Nothing reaches Jira until it's approved.</li>
        </ol>
        {proposal && (proposal.assumptions.length > 0 || proposal.open_questions.length > 0) && (
          <>
            <div className="divider" />
            {proposal.assumptions.length > 0 && <RetroList title="Assumptions" items={proposal.assumptions} tone="muted" />}
            {proposal.open_questions.length > 0 && (
              <div style={{ marginTop: 12 }}><RetroList title="Open questions" items={proposal.open_questions} tone="warn" /></div>
            )}
            <p className="xs faint" style={{ marginBottom: 0 }}>Drafted by: {proposal.drafted_by === "parser" ? "offline parser" : proposal.drafted_by}</p>
          </>
        )}
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ why is it late?

interface Loss {
  cause: string;
  label: string;
  days: number;
  share: number;
  issues: string[];
  evidence: string[];
}

interface ClaimCheck {
  claim: string;
  source: string;
  said_by: string | null;
  said_on: string | null;
  verdict: "contradicted" | "supported" | "unverifiable";
  reality: string[];
}

interface IssueStory {
  key: string;
  summary: string;
  assignee: string | null;
  status: string;
  days_lost: number;
  segments: { status: string; start: string; end: string; days: number }[];
  events: string[];
}

interface DelayReport {
  sprint: string | null;
  target: string | null;
  projected_finish: string | null;
  slip_days: number;
  headline: string;
  total_days_lost: number;
  losses: Loss[];
  stories: IssueStory[];
  claims: ClaimCheck[];
  warnings: string[];
}

const VERDICT: Record<ClaimCheck["verdict"], { tone: string; label: string }> = {
  contradicted: { tone: "bad", label: "Contradicted by data" },
  supported: { tone: "good", label: "Matches the data" },
  unverifiable: { tone: "muted", label: "Can't verify" },
};

function segmentTone(status: string): string {
  const s = status.toLowerCase();
  if (["blocked", "on hold", "impeded", "waiting"].includes(s)) return "bad";
  if (["done", "closed", "resolved"].includes(s)) return "good";
  if (s.includes("review") || s === "qa" || s === "testing") return "info";
  if (s.includes("progress") || s === "doing") return "accent";
  return "muted";
}

function ClaimList({ claims }: { claims: ClaimCheck[] }) {
  return (
    <ul className="list">
      {claims.map((c, i) => (
        <li key={`${c.claim}-${i}`} style={{ alignItems: "flex-start" }}>
          <div className="grow stack" style={{ gap: 6 }}>
            <div className="row between">
              <Badge value={VERDICT[c.verdict].tone} label={VERDICT[c.verdict].label} />
              <span className="xs faint">
                {c.said_by ?? "—"}{c.said_on ? ` · ${formatDay(c.said_on)}` : ""}
              </span>
            </div>
            <blockquote className="quote">“{c.claim}”</blockquote>
            <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
              {c.reality.map((r) => <li key={r}>{r}</li>)}
            </ul>
            <span className="xs faint">Source: {c.source}</span>
          </div>
        </li>
      ))}
    </ul>
  );
}

export function DelayPanel({ projectKey }: { projectKey: string }) {
  const report = useQuery({
    queryKey: ["delay", projectKey],
    queryFn: () => api<DelayReport>(`${base(projectKey)}/delay`),
  });
  const [statement, setStatement] = useState("");
  const check = useMutation({
    mutationFn: () => api<ClaimCheck[]>(`${base(projectKey)}/delay/check`, { body: { statement } }),
  });

  if (report.isLoading) return <Spinner label="Reading Jira history, pull requests, leave and e-mail…" />;
  if (report.error || !report.data) return <ErrorNote error={report.error ?? new Error("No data")} />;
  const r = report.data;
  const max = Math.max(1, ...r.losses.map((l) => l.days));
  const contradicted = r.claims.filter((c) => c.verdict === "contradicted").length;

  return (
    <>
      <section className={`card verdict ${r.slip_days > 0 ? "late" : "ok"}`}>
        <div className="row between">
          <div className="eyebrow" style={{ margin: 0 }}>What the data says</div>
          <Badge value={r.slip_days > 0 ? "bad" : "good"} label={r.slip_days > 0 ? `${r.slip_days} working days late` : "On time so far"} />
        </div>
        <p className="verdict-headline">{r.headline}</p>
        <div className="row xs muted">
          <span>Target <strong className="num">{formatDay(r.target)}</strong></span>
          <span>·</span>
          <span>Projected <strong className="num">{formatDay(r.projected_finish)}</strong></span>
          <span>·</span>
          <span><strong className="num">{r.total_days_lost}</strong> person-days lost{r.sprint ? ` in ${r.sprint}` : ""}</span>
          {contradicted > 0 && <><span>·</span><span className="tone-bad"><strong className="num">{contradicted}</strong> status claim(s) contradicted</span></>}
        </div>
      </section>

      <div className="grid grid-2" style={{ alignItems: "start" }}>
        <Card title="Where the time went">
          {r.losses.length === 0 ? (
            <Empty>No lost time found in Jira, pull requests, leave or incidents.</Empty>
          ) : (
            <div className="stack">
              {r.losses.map((loss) => (
                <details key={loss.cause} className="loss">
                  <summary>
                    <span className="loss-label">
                      <strong className="small">{loss.label}</strong>
                      <span className="xs faint">{loss.issues.join(", ")}</span>
                    </span>
                    <span className="loss-bar"><span style={{ width: `${(loss.days / max) * 100}%` }} /></span>
                    <span className="small num loss-days">{loss.days}d · {Math.round(loss.share * 100)}%</span>
                  </summary>
                  <ul className="xs muted" style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                    {loss.evidence.map((e) => <li key={e}>{e}</li>)}
                  </ul>
                </details>
              ))}
              <p className="xs faint" style={{ margin: 0 }}>
                Person working days since the sprint started. Causes can overlap, so shares rank them rather than add up exactly. Click a cause for the evidence.
              </p>
            </div>
          )}
        </Card>

        <Card title="Reported vs actual">
          {r.claims.length === 0 ? (
            <Empty>No status claims found in Jira comments or e-mails about this project.</Empty>
          ) : (
            <ClaimList claims={r.claims} />
          )}
        </Card>
      </div>

      <Card title="Check what you were told">
        <form className="input-row" onSubmit={(e) => { e.preventDefault(); if (statement.trim().length >= 3) check.mutate(); }}>
          <input
            aria-label="Statement to check"
            placeholder={`e.g. “We're on track, no blockers, ${projectKey}-2 done by Friday”`}
            value={statement}
            maxLength={2000}
            onChange={(e) => setStatement(e.target.value)}
          />
          <button type="submit" className="button" disabled={statement.trim().length < 3 || check.isPending}>
            {check.isPending ? "Checking…" : "Check against data"}
          </button>
        </form>
        <ErrorNote error={check.error} />
        {check.data && <div style={{ marginTop: 16 }}><ClaimList claims={check.data} /></div>}
      </Card>

      <Card title="Issue timelines">
        <ul className="list">
          {r.stories.filter((s) => s.days_lost > 0 || s.segments.length > 1).map((s) => (
            <li key={s.key} style={{ alignItems: "flex-start" }}>
              <div className="grow stack" style={{ gap: 6 }}>
                <div className="row between">
                  <span className="small"><strong>{s.key}</strong> <span className="muted">{s.summary}</span></span>
                  <span className="row xs muted">
                    {s.assignee ?? "Unassigned"} · <Badge value={segmentTone(s.status)} label={s.status} />
                    {s.days_lost > 0 && <Badge value="bad" label={`${s.days_lost}d lost`} />}
                  </span>
                </div>
                <div className="timeline-bar" role="img"
                     aria-label={s.segments.map((g) => `${g.status} ${g.days} days`).join(", ")}>
                  {s.segments.filter((g) => g.days > 0).map((g, i) => (
                    <span key={`${g.status}-${i}`} className={`seg seg-${segmentTone(g.status)}`} style={{ flexGrow: g.days }}
                          title={`${g.status}: ${formatDay(g.start)} → ${formatDay(g.end)} (${g.days}d)`}>
                      {g.days >= 2 ? `${g.status} ${g.days}d` : ""}
                    </span>
                  ))}
                </div>
                {s.events.length > 0 && (
                  <details>
                    <summary className="xs muted">What happened ({s.events.length})</summary>
                    <ul className="xs muted" style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                      {s.events.map((e) => <li key={e}>{e}</li>)}
                    </ul>
                  </details>
                )}
              </div>
            </li>
          ))}
        </ul>
      </Card>
      {r.warnings.length > 0 && <div className="notice small">Some evidence was unavailable: {r.warnings.join(" · ")}</div>}
    </>
  );
}
