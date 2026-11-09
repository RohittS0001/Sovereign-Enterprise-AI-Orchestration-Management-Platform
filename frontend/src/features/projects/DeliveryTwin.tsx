import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../shared/api/client";
import { Badge, Card, ErrorNote, Markdown } from "../../shared/ui/components";

export interface Simulation {
  scenario: string;
  target_date: string | null;
  probability_by_target: number | null;
  p50: string | null;
  p80: string | null;
  p95: string | null;
  finish_rate: number;
  remaining_points: number;
  remaining_issues: number;
  efficiency: number;
  blocked_issues: string[];
  rework_chance: number;
  histogram: [string, number][];
  trials: number;
  drivers: string[];
}

interface NegotiationPlan {
  target_date: string | null;
  baseline: Simulation;
  options: { id: string; title: string; tradeoff: string; result: Simulation; meets_goal: boolean }[];
  recommended: string | null;
  message_markdown: string;
  generated_by_model: string | null;
}

const pct = (value: number | null) => (value === null ? "—" : `${Math.round(value * 100)}%`);

/** Completion-date distribution as accessible CSS bars (no chart library needed). */
function Histogram({ sim }: { sim: Simulation }) {
  const max = Math.max(1, ...sim.histogram.map(([, n]) => n));
  return (
    <div className="histogram" role="img" aria-label={`Likely finish ${sim.p50}, 80% by ${sim.p80}`}>
      {sim.histogram.map(([day, n]) => (
        <span
          key={day}
          title={`${day}: ${Math.round((n / sim.trials) * 100)}% of trials`}
          className={sim.target_date && day <= sim.target_date ? "hit" : undefined}
          style={{ height: `${(n / max) * 100}%` }}
        />
      ))}
    </div>
  );
}

export function DeliveryTwin({ projectKey }: { projectKey: string }) {
  const [target, setTarget] = useState("");
  const [addPeople, setAddPeople] = useState(0);
  const [cutPoints, setCutPoints] = useState(0);
  const [unblock, setUnblock] = useState(false);
  const base = `/projects/${encodeURIComponent(projectKey)}`;
  const simulate = useMutation({
    mutationFn: () =>
      api<Simulation[]>(`${base}/simulate`, {
        body: {
          target_date: target || null,
          scenarios:
            addPeople || cutPoints || unblock
              ? [{ name: "what-if", add_people: addPeople, cut_points: cutPoints, resolve_blockers: unblock }]
              : [],
        },
      }),
  });
  const negotiate = useMutation({
    mutationFn: () => api<NegotiationPlan>(`${base}/negotiate`, { body: { target_date: target || null } }),
  });
  const [baseline, whatIf] = simulate.data ?? [];

  return (
    <Card title="Delivery forecast (digital twin)">
      <form
        className="row"
        style={{ flexWrap: "wrap", alignItems: "end" }}
        onSubmit={(e) => {
          e.preventDefault();
          simulate.mutate();
        }}
      >
        <label>
          Target date
          <input type="date" value={target} onChange={(e) => setTarget(e.target.value)} />
        </label>
        <label>
          Add people
          <input type="number" min={0} max={10} value={addPeople} onChange={(e) => setAddPeople(Number(e.target.value))} />
        </label>
        <label>
          Cut points
          <input type="number" min={0} value={cutPoints} onChange={(e) => setCutPoints(Number(e.target.value))} />
        </label>
        <label className="row">
          <input type="checkbox" checked={unblock} onChange={(e) => setUnblock(e.target.checked)} /> Blockers cleared
        </label>
        <button className="button" type="submit" disabled={simulate.isPending}>
          {simulate.isPending ? "Simulating…" : "Run simulation"}
        </button>
        <button className="button secondary" type="button" onClick={() => negotiate.mutate()} disabled={negotiate.isPending}>
          {negotiate.isPending ? "Working…" : "Negotiation options"}
        </button>
      </form>
      <ErrorNote error={simulate.error ?? negotiate.error} />

      {baseline && (
        <div className="grid grid-2" style={{ marginTop: 12 }}>
          {[baseline, whatIf].filter(Boolean).map((sim) => (
            <div key={sim!.scenario}>
              <h3 style={{ textTransform: "capitalize" }}>{sim!.scenario}</h3>
              <div className="row">
                <span className="stat">{pct(sim!.probability_by_target)}</span>
                <span className="small muted">chance by {sim!.target_date ?? "—"}</span>
              </div>
              <div className="small">
                Likely <strong>{sim!.p50}</strong> · 80% by <strong>{sim!.p80}</strong> · 95% by {sim!.p95}
              </div>
              <Histogram sim={sim!} />
              <div className="small muted">
                {sim!.remaining_points} pts in {sim!.remaining_issues} issues · efficiency {pct(sim!.efficiency)} ·{" "}
                {sim!.trials} trials
              </div>
              {sim!.drivers.length > 0 && (
                <ul className="small">
                  {sim!.drivers.map((d) => (
                    <li key={d}>{d}</li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
      )}

      {negotiate.data && (
        <div style={{ marginTop: 12 }}>
          <h3>Options for {negotiate.data.target_date ?? "the target"}</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Option</th>
                  <th>Chance</th>
                  <th>80% by</th>
                  <th>Trade-off</th>
                </tr>
              </thead>
              <tbody>
                {negotiate.data.options.map((o) => (
                  <tr key={o.id}>
                    <td>
                      {o.title} {o.id === negotiate.data.recommended && <Badge value="healthy" label="recommended" />}
                    </td>
                    <td>{pct(o.result.probability_by_target)}</td>
                    <td>{o.result.p80}</td>
                    <td className="small">{o.tradeoff}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h3>Draft note to stakeholders</h3>
          <div className="small muted">{negotiate.data.generated_by_model ? `Written by ${negotiate.data.generated_by_model}` : "Template"} · not sent</div>
          <Markdown text={negotiate.data.message_markdown} />
        </div>
      )}
    </Card>
  );
}
