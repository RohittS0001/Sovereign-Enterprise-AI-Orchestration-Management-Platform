import { useQuery } from "@tanstack/react-query";
import { api } from "../../shared/api/client";
import { Badge, Card, ErrorNote, Spinner } from "../../shared/ui/components";

interface Control {
  id: string;
  title: string;
  soc2: string[];
  iso27001: string[];
  status: "pass" | "warn" | "fail" | "unknown";
  evidence: string[];
  gaps: string[];
}

const TONE = { pass: "healthy", warn: "warn", fail: "bad", unknown: "unknown" } as const;

/** SOC 2 / ISO 27001 evidence collected live from the platform. */
export function ComplianceCard() {
  const report = useQuery({
    queryKey: ["compliance"],
    queryFn: () => api<{ generated_at: string; summary: Record<string, number>; controls: Control[] }>("/compliance/report"),
  });
  return (
    <Card title="Compliance evidence (SOC 2 / ISO 27001)">
      {report.isLoading && <Spinner label="Collecting evidence…" />}
      <ErrorNote error={report.error} />
      {report.data && (
        <p className="small muted">
          {Object.entries(report.data.summary)
            .map(([status, count]) => `${count} ${status}`)
            .join(" · ")}{" "}
          — collected automatically; review before submitting to an auditor.
        </p>
      )}
      {report.data?.controls.map((c) => (
        <details key={c.id} style={{ marginBottom: 8 }}>
          <summary>
            <Badge value={TONE[c.status]} label={c.status} /> <strong>{c.title}</strong>{" "}
            <span className="small muted">
              SOC 2 {c.soc2.join(", ")} · ISO {c.iso27001.join(", ")}
            </span>
          </summary>
          <ul className="small">
            {c.evidence.map((e) => (
              <li key={e}>{e}</li>
            ))}
            {c.gaps.map((g) => (
              <li key={g}>
                <strong>Gap:</strong> {g}
              </li>
            ))}
          </ul>
        </details>
      ))}
    </Card>
  );
}
