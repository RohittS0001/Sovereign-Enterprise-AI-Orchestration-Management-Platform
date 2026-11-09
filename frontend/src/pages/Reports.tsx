import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Page, Report, ReportSummary } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Badge, Card, Empty, ErrorNote, Markdown, Spinner, formatDate } from "../components/ui";

function ReportView({ id }: { id: string }) {
  const { can } = useAuth();
  const [recipients, setRecipients] = useState("");
  const report = useQuery({ queryKey: ["report", id], queryFn: () => api<Report>(`/reports/${id}`) });
  const draft = useMutation({
    mutationFn: () =>
      api<{ status: string; error: string | null }>(`/reports/${id}/email-draft`, {
        body: { to: recipients.split(",").map((r) => r.trim()).filter(Boolean) },
      }),
  });

  if (report.isLoading) return <Spinner />;
  if (!report.data) return <ErrorNote error={report.error} />;
  const r = report.data;
  return (
    <Card
      title={`${r.project_key} · week of ${r.period_start}`}
      actions={<Badge value={r.risk_level} label={`risk ${r.risk_score}`} />}
    >
      <div className="small muted" style={{ marginBottom: 10 }}>
        {r.trigger} · {r.generated_by_model ? `written by ${r.generated_by_model}` : "template"} ·{" "}
        {formatDate(r.created_at)}
      </div>
      <Markdown text={r.content_markdown} />
      {can("email:send") && (
        <div className="stack" style={{ marginTop: 16 }}>
          <label>
            Email as a Gmail draft (comma-separated; empty = project recipients)
            <input value={recipients} onChange={(e) => setRecipients(e.target.value)} placeholder="lead@company.com" />
          </label>
          <div>
            <button className="button secondary" onClick={() => draft.mutate()} disabled={draft.isPending}>
              Create draft
            </button>
          </div>
          {draft.data?.status === "success" && <div className="notice">Draft created — review and send it from Gmail.</div>}
          {draft.data?.error && <ErrorNote error={new Error(draft.data.error)} />}
          <ErrorNote error={draft.error} />
        </div>
      )}
    </Card>
  );
}

export function ReportsPage() {
  const [params, setParams] = useSearchParams();
  const open = params.get("open");
  const reports = useQuery({ queryKey: ["reports"], queryFn: () => api<Page<ReportSummary>>("/reports?limit=50") });

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Reports</h1>
          <p>Weekly status reports, generated on demand or on schedule.</p>
        </div>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "300px minmax(0, 1fr)" }}>
        <Card title="All reports">
          {reports.isLoading && <Spinner />}
          <ErrorNote error={reports.error} />
          {reports.data?.items.length === 0 && <Empty>No reports yet. Generate one from a project page.</Empty>}
          <ul className="history" style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {reports.data?.items.map((r) => (
              <li key={r.id}>
                <button onClick={() => setParams({ open: r.id })} aria-current={open === r.id}>
                  <div>
                    {r.project_key} · {r.period_start}
                  </div>
                  <div className="row small muted">
                    <Badge value={r.risk_level} /> {r.trigger}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </Card>
        <div>{open ? <ReportView id={open} /> : <Empty>Select a report.</Empty>}</div>
      </div>
    </>
  );
}
