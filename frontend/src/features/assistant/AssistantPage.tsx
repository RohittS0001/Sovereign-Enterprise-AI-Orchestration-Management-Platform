import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../shared/api/client";
import { streamAgentQuery, type AgentEvent } from "../../shared/api/stream";
import type { AgentRun, AgentStep, Page } from "../../shared/api/types";
import { Badge, Card, Empty, ErrorNote, Markdown, PageHeader, Spinner, formatDate } from "../../shared/ui/components";
import { Icon } from "../../shared/ui/icons";

const EXAMPLES = [
  "Is the PHX project at risk this sprint?",
  "Who on the Phoenix team is overloaded, considering leave?",
  "What does our escalation policy say if a vendor gives no fix date?",
];

function StepList({ steps }: { steps: AgentStep[] }) {
  if (!steps.length) return null;
  return (
    <ol className="timeline" aria-label="Agent steps">
      {steps.map((step) => (
        <li key={step.id}>
          <span className="step-id">{step.id}</span>
          <div>
            <code>{step.tool}</code>
            <div className="muted small">{step.purpose}</div>
            {step.error && <div className="small" style={{ color: "var(--bad)" }}>{step.error}</div>}
            {step.suspicious && <div className="notice small">Content looked like a prompt-injection attempt.</div>}
            {step.approval_id && (
              <div className="small">
                Waiting for approval — <Link to="/approvals">review it</Link>
              </div>
            )}
          </div>
          <Badge value={step.status} />
        </li>
      ))}
    </ol>
  );
}

interface TeamRun {
  query: string;
  rationale: string;
  answer: string;
  specialists: { specialist: string; question: string; run_id: string; status: string; answer: string | null;
                 pending_approvals: string[]; tools: string[] }[];
}

export function AssistantPage() {
  const queryClient = useQueryClient();
  const [question, setQuestion] = useState("");
  const [steps, setSteps] = useState<AgentStep[]>([]);
  const [phase, setPhase] = useState<string | null>(null);
  const [run, setRun] = useState<AgentRun | null>(null);
  const [asked, setAsked] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const [mode, setMode] = useState<"single" | "team">("single");
  const [team, setTeam] = useState<TeamRun | null>(null);

  const history = useQuery({
    queryKey: ["runs"],
    queryFn: () => api<Page<AgentRun>>("/agent/runs?limit=15"),
  });

  function onEvent(event: AgentEvent) {
    if (event.type === "step") {
      const { step_id, ...rest } = event.step;
      setSteps((current) => [...current, { id: step_id, ...rest }]);
      setPhase("Working…");
    } else if (event.type === "phase") {
      setPhase("Writing the answer…");
    } else if (event.type === "done") {
      setRun(event.run);
      setSteps(event.run.steps);
    } else if (event.type === "error") {
      setError(new Error(event.message));
    }
  }

  async function ask(text: string) {
    if (!text.trim() || busy) return;
    abort.current = new AbortController();
    setBusy(true);
    setAsked(text.trim());
    setSteps([]);
    setRun(null);
    setTeam(null);
    setError(null);
    setPhase("Planning…");
    try {
      await streamAgentQuery(text.trim(), onEvent, abort.current.signal);
      setQuestion("");
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      void queryClient.invalidateQueries({ queryKey: ["approvals"] });
    } catch (err) {
      if ((err as Error).name !== "AbortError") setError(err);
    } finally {
      setBusy(false);
      setPhase(null);
    }
  }

  function showRun(previous: AgentRun) {
    setTeam(null);
    setAsked(previous.query);
    setRun(previous);
    setSteps(previous.steps);
    setError(null);
  }

  async function askTeam(text: string) {
    if (!text.trim() || busy) return;
    setBusy(true);
    setAsked(text.trim());
    setSteps([]);
    setRun(null);
    setTeam(null);
    setError(null);
    setPhase("The supervisor is routing your question to specialists…");
    try {
      setTeam(await api<TeamRun>("/agent/team", { body: { query: text.trim() } }));
      setQuestion("");
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      void queryClient.invalidateQueries({ queryKey: ["approvals"] });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
      setPhase(null);
    }
  }

  const send = (text: string) => (mode === "team" ? askTeam(text) : ask(text));

  return (
    <>
      <PageHeader
        eyebrow="Workspace"
        title="Assistant"
        description="Ask about projects, people, code and documents. Changes always wait for approval."
      />
      <div className="split">
        <div>
          <form className="composer" onSubmit={(e) => { e.preventDefault(); void send(question); }}>
            <label>
              <span className="sr-only">Question</span>
              <textarea
                aria-label="Question"
                placeholder="Ask a question… (Enter to send, Shift+Enter for a new line)"
                value={question}
                maxLength={4000}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send(question);
                  }
                }}
              />
            </label>
            <div className="composer-bar">
              <div className="segmented" role="group" aria-label="Mode">
                <button type="button" aria-pressed={mode === "single"} onClick={() => setMode("single")}>Single agent</button>
                <button type="button" aria-pressed={mode === "team"} onClick={() => setMode("team")}>Team of specialists</button>
              </div>
              <span className="xs faint" style={{ marginLeft: "auto" }}>{question.length}/4000</span>
              {busy && mode === "single" ? (
                <button type="button" className="button secondary" onClick={() => abort.current?.abort()}>
                  <Icon name="stop" /> Stop
                </button>
              ) : (
                <button type="submit" className="button" disabled={!question.trim() || busy}>
                  <Icon name="send" /> Ask
                </button>
              )}
            </div>
          </form>
          {!asked && (
            <div className="suggestions">
              {EXAMPLES.map((example) => (
                <button key={example} type="button" className="chip" onClick={() => void send(example)}>
                  {example}
                </button>
              ))}
            </div>
          )}

          {asked && (
            <Card title={asked} actions={run && <Badge value={run.status} />} className="answer-card">
              <div className="stack">
                <StepList steps={steps} />
                {phase && <Spinner label={phase} />}
                <ErrorNote error={error} />
                {run?.flagged_injection && (
                  <div className="notice">Some retrieved content looked like a prompt-injection attempt.</div>
                )}
                {run?.error && <ErrorNote error={new Error(run.error)} />}
                {run?.answer && <Markdown text={run.answer} />}
                {run && run.citations.length > 0 && (
                  <div className="small muted">
                    Sources: {run.citations.map((c) => <span key={c} className="cite">{c}</span>)}
                  </div>
                )}
                {run && run.pending_approvals.length > 0 && (
                  <div className="notice">
                    {run.pending_approvals.length} action(s) are waiting for approval.{" "}
                    <Link to="/approvals">Open approvals</Link>
                  </div>
                )}
                {run && (
                  <div className="xs faint">
                    {run.usage.prompt_tokens + run.usage.completion_tokens} tokens · {formatDate(run.started_at)}
                  </div>
                )}
                {team && (
                  <>
                    <Markdown text={team.answer} />
                    <div className="divider" />
                    <div className="small muted">{team.rationale || "Specialists consulted"}</div>
                    <ul className="list">
                      {team.specialists.map((s) => (
                        <li key={s.run_id} style={{ alignItems: "flex-start" }}>
                          <Badge value="demo" label={s.specialist} />
                          <div className="grow">
                            <div className="small"><strong>{s.question}</strong></div>
                            <div className="xs faint">{s.tools.join(", ") || "no tools"} · {s.status.replace("_", " ")}</div>
                          </div>
                          {s.pending_approvals.length > 0 && <Badge value="pending" label={`${s.pending_approvals.length} to approve`} />}
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
            </Card>
          )}
        </div>

        <Card title="Recent questions">
          {history.isLoading && <Spinner />}
          <ErrorNote error={history.error} />
          {history.data?.items.length === 0 && <Empty>No questions yet.</Empty>}
          <ul className="history" style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {history.data?.items.map((item) => (
              <li key={item.run_id}>
                <button onClick={() => showRun(item)} aria-current={run?.run_id === item.run_id}>
                  <div className="truncate">{item.query}</div>
                  <div className="row xs muted">
                    <Badge value={item.status} /> {formatDate(item.started_at)}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </>
  );
}
