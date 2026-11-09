import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../../shared/api/client";
import { streamAgentQuery, type AgentEvent } from "../../shared/api/stream";
import type { AgentRun, AgentStep, Page } from "../../shared/api/types";
import { Badge, Card, Empty, ErrorNote, Markdown, Spinner, formatDate } from "../../shared/ui/components";

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

  function submit(event: FormEvent) {
    event.preventDefault();
    void ask(question);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void ask(question);
    }
  }

  function showRun(previous: AgentRun) {
    setAsked(previous.query);
    setRun(previous);
    setSteps(previous.steps);
    setError(null);
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Assistant</h1>
          <p>Ask about projects, people, code and documents. Changes always wait for approval.</p>
        </div>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 1fr) 280px" }}>
        <div>
          <Card>
            <form className="chat-form" onSubmit={submit}>
              <label style={{ flex: 1 }}>
                <span className="sr-only">Question</span>
                <textarea
                  aria-label="Question"
                  placeholder="Ask a question… (Enter to send, Shift+Enter for a new line)"
                  value={question}
                  maxLength={4000}
                  onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={onKeyDown}
                />
              </label>
              {busy ? (
                <button type="button" className="button secondary" onClick={() => abort.current?.abort()}>
                  Stop
                </button>
              ) : (
                <button type="submit" className="button" disabled={!question.trim()}>
                  Ask
                </button>
              )}
            </form>
            {!asked && (
              <div className="row small" style={{ marginTop: 10 }}>
                <span className="muted">Try:</span>
                {EXAMPLES.map((example) => (
                  <button key={example} className="button ghost small" onClick={() => void ask(example)}>
                    {example}
                  </button>
                ))}
              </div>
            )}
          </Card>

          {asked && (
            <Card
              title={asked}
              actions={run && <Badge value={run.status} />}
            >
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
                  <div className="small muted">
                    {run.usage.prompt_tokens + run.usage.completion_tokens} tokens · {formatDate(run.started_at)}
                  </div>
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
                <button onClick={() => showRun(item)}>
                  <div>{item.query}</div>
                  <div className="row small muted">
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
