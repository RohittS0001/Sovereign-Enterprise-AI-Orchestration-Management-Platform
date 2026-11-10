import { useQuery } from "@tanstack/react-query";
import { api } from "../../shared/api/client";
import { Badge, Card, ErrorNote, Kpi, PageHeader, Spinner } from "../../shared/ui/components";

interface Control {
  id: string;
  title: string;
  soc2: string[];
  iso27001: string[];
  status: "pass" | "warn" | "fail" | "unknown";
  evidence: string[];
  gaps: string[];
}
interface Report {
  generated_at: string;
  summary: Record<"pass" | "warn" | "fail" | "unknown", number>;
  controls: Control[];
}

/** SOC 2 / ISO 27001 evidence collected live from the platform. */
export function CompliancePage() {
  const report = useQuery({ queryKey: ["compliance"], queryFn: () => api<Report>("/compliance/report") });
  const s = report.data?.summary;
  return (
    <>
      <PageHeader
        eyebrow="Insights"
        title="Compliance evidence"
        description="Controls mapped to SOC 2 and ISO 27001:2022, with evidence from the audit chain, roles, approvals, code review, incidents and configuration. Review before submitting to an auditor."
      />
      {report.isLoading && <Spinner label="Collecting evidence…" />}
      <ErrorNote error={report.error} />
      {s && (
        <div className="grid grid-4">
          <Kpi label="Passing" value={s.pass} tone="good" />
          <Kpi label="Needs attention" value={s.warn} tone={s.warn ? "warn" : undefined} />
          <Kpi label="Failing" value={s.fail} tone={s.fail ? "bad" : undefined} />
          <Kpi label="No evidence yet" value={s.unknown} />
        </div>
      )}
      <ComplianceControls controls={report.data?.controls ?? []} />
    </>
  );
}

const TONE = { pass: "healthy", warn: "warn", fail: "bad", unknown: "unknown" } as const;

export function ComplianceControls({ controls }: { controls: Control[] }) {
  return (
    <>
      {controls.map((c) => (
        <Card key={c.id}>
          <details open={c.status === "fail" || c.status === "warn"}>
            <summary>
              <Badge value={TONE[c.status]} label={c.status} />
              <strong>{c.title}</strong>
              <span className="xs faint" style={{ marginLeft: "auto" }}>
                SOC 2 {c.soc2.join(", ")} · ISO {c.iso27001.join(", ")}
              </span>
            </summary>
            <ul className="small" style={{ margin: "8px 0 0", paddingLeft: 18 }}>
              {c.evidence.map((e) => <li key={e}>{e}</li>)}
              {c.gaps.map((g) => (
                <li key={g} className="tone-warn"><strong>Gap:</strong> {g}</li>
              ))}
            </ul>
          </details>
        </Card>
      ))}
    </>
  );
}
