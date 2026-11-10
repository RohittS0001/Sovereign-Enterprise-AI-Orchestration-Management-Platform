import type { ProjectHealth } from "../../shared/api/types";
import { Badge, Card, Empty } from "../../shared/ui/components";

/** Incidents, failing deploys, quality gates and noisy errors from the ops connectors. */
export function ProductionCard({ production }: { production: NonNullable<ProjectHealth["production"]> }) {
  const { incidents, errors, deployments, quality_gates: gates } = production;
  const failing = deployments.filter((d) => d.status !== "success");
  const quiet = !incidents.length && !failing.length && !gates.some((g) => g.status === "ERROR") && !errors.length;
  return (
    <Card title="Production">
      {quiet && <Empty>No open incidents, failing deploys or quality gate problems.</Empty>}
      <ul className="small">
        {incidents.map((i) => (
          <li key={`${i.source}-${i.id}`}>
            <Badge value={i.severity === "high" ? "bad" : "warn"} label={`${i.source} ${i.status}`} />{" "}
            {i.url ? <a href={i.url} target="_blank" rel="noreferrer">{i.title}</a> : i.title}
          </li>
        ))}
        {failing.map((d) => (
          <li key={`${d.source}-${d.name}`}>
            <Badge value="bad" label={d.status.replace("_", " ")} /> {d.name} <span className="muted">{d.detail}</span>
          </li>
        ))}
        {gates.filter((g) => g.status === "ERROR").map((g) => (
          <li key={g.project}>
            <Badge value="bad" label="quality gate" /> {g.project}: {g.failing.join("; ")}
          </li>
        ))}
        {errors.slice(0, 5).map((e) => (
          <li key={e.id}>
            <Badge value="warn" label={`${e.events} events`} /> {e.title} <span className="muted">({e.users} users)</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}
