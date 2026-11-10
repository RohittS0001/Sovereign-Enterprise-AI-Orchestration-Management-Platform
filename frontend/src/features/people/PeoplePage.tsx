import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../shared/api/client";
import { Avatar, Badge, Card, Empty, ErrorNote, Kpi, PageHeader, Spinner } from "../../shared/ui/components";
import { Icon } from "../../shared/ui/icons";

interface Expert {
  person: string;
  score: number;
  evidence: string[];
}
interface Absence {
  person: string;
  sole_open_work: string[];
  blocked_behind: string[];
  concentrated_repositories: string[];
  documents_owned: string[];
  suggested_backups: Expert[];
  severity: "low" | "medium" | "high";
}
interface Summary {
  people: number;
  items: number;
  edges: number;
  bus_factor: Record<string, number>;
}

/** Org knowledge graph: who knows what, what breaks if someone is away, bus factor. */
export function PeoplePage() {
  const [topic, setTopic] = useState("");
  const [person, setPerson] = useState("");
  const summary = useQuery({ queryKey: ["graph-summary"], queryFn: () => api<Summary>("/graph/summary") });
  const experts = useMutation({
    mutationFn: (q: string) => api<Expert[]>(`/graph/who-knows?topic=${encodeURIComponent(q)}`),
  });
  const impact = useMutation({
    mutationFn: (name: string) => api<Absence>(`/graph/people/${encodeURIComponent(name)}/impact`),
  });
  const risky = Object.entries(summary.data?.bus_factor ?? {}).filter(([, n]) => n <= 1);

  return (
    <>
      <PageHeader
        eyebrow="Insights"
        title="People & expertise"
        description="Built from delivered issues, merged pull requests and owned documents across every active project."
      />
      <ErrorNote error={summary.error} />
      <div className="grid grid-3">
        <Kpi icon="users" label="People in the graph" value={summary.data?.people ?? "…"} />
        <Kpi icon="network" label="Work items linked" value={summary.data?.items ?? "…"} foot={`${summary.data?.edges ?? "…"} relationships`} />
        <Kpi icon="alert" label="Single-owner repositories" value={summary.data ? risky.length : "…"}
             tone={risky.length ? "warn" : "good"} foot={risky.map(([r]) => r).join(", ") || "Every repository has backup"} />
      </div>
      <div className="grid grid-2">
        <Card title={<><Icon name="search" width={16} height={16} /> Who knows…</>}>
          <form className="input-row" onSubmit={(e) => { e.preventDefault(); if (topic.trim().length >= 2) experts.mutate(topic.trim()); }}>
            <input aria-label="Topic" placeholder="e.g. refunds, capture timeouts" value={topic} onChange={(e) => setTopic(e.target.value)} />
            <button className="button" type="submit" disabled={experts.isPending}>Find experts</button>
          </form>
          <ErrorNote error={experts.error} />
          {experts.isPending && <Spinner />}
          {experts.data?.length === 0 && <Empty>Nobody has worked on that yet.</Empty>}
          <ul className="list" style={{ marginTop: 16 }}>
            {experts.data?.map((e, rank) => (
              <li key={e.person} style={{ alignItems: "flex-start" }}>
                <Avatar name={e.person} />
                <div className="grow">
                  <div className="row between">
                    <strong>{e.person}</strong>
                    <span className="xs faint num">#{rank + 1} · score {e.score}</span>
                  </div>
                  <ul className="xs muted" style={{ margin: "4px 0 0", paddingLeft: 16 }}>
                    {e.evidence.slice(0, 3).map((line) => <li key={line}>{line}</li>)}
                  </ul>
                </div>
              </li>
            ))}
          </ul>
        </Card>
        <Card title={<><Icon name="alert" width={16} height={16} /> What breaks if someone is away?</>}>
          <form className="input-row" onSubmit={(e) => { e.preventDefault(); if (person.trim()) impact.mutate(person.trim()); }}>
            <input aria-label="Person" placeholder="Name as in Jira, e.g. Arjun M" value={person} onChange={(e) => setPerson(e.target.value)} />
            <button className="button" type="submit" disabled={impact.isPending}>Assess impact</button>
          </form>
          <ErrorNote error={impact.error} />
          {impact.data && (
            <div className="stack" style={{ marginTop: 16 }}>
              <div className="row">
                <Avatar name={impact.data.person} />
                <strong>{impact.data.person}</strong>
                <Badge value={impact.data.severity} label={`${impact.data.severity} impact`} />
              </div>
              <Section title="Open work only they hold" items={impact.data.sole_open_work} />
              <Section title="Blocked behind their work" items={impact.data.blocked_behind} />
              <Section title="Repositories they mostly wrote" items={impact.data.concentrated_repositories} />
              <Section title="Documents they own" items={impact.data.documents_owned} />
              <div>
                <div className="small muted" style={{ marginBottom: 6 }}>Suggested backups</div>
                <div className="row">
                  {impact.data.suggested_backups.length === 0 && <span className="small faint">None found</span>}
                  {impact.data.suggested_backups.map((b) => (
                    <span key={b.person} className="badge badge-info">{b.person}</span>
                  ))}
                </div>
              </div>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}

function Section({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <div className="small muted" style={{ marginBottom: 4 }}>{title}</div>
      {items.length === 0 ? <span className="small faint">None</span> : (
        <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{items.map((i) => <li key={i}>{i}</li>)}</ul>
      )}
    </div>
  );
}
