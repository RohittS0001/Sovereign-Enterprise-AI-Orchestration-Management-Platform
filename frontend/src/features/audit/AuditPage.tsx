import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../shared/api/client";
import type { AuditEvent, Page } from "../../shared/api/types";
import { Badge, Card, Empty, ErrorNote, Spinner, formatDate } from "../../shared/ui/components";

const ACTIONS = ["", "auth.login", "tool.invoke", "agent.run", "approval.decide", "approval.execute",
  "knowledge.ingest", "report.generate", "user.update"];

export function AuditPage() {
  const [action, setAction] = useState("");
  const events = useQuery({
    queryKey: ["audit", action],
    queryFn: () => api<Page<AuditEvent>>(`/audit?limit=100${action ? `&action=${action}` : ""}`),
  });
  const verify = useMutation({
    mutationFn: () => api<{ valid: boolean; checked: number; first_invalid_seq: number | null; reason: string | null }>("/audit/verify"),
  });

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Audit log</h1>
          <p>Every login, tool call, agent run and approval — chained so edits are detectable.</p>
        </div>
        <div className="row">
          <label>
            Action
            <select value={action} onChange={(e) => setAction(e.target.value)}>
              {ACTIONS.map((a) => (
                <option key={a} value={a}>
                  {a || "all"}
                </option>
              ))}
            </select>
          </label>
          <button className="button secondary" onClick={() => verify.mutate()} disabled={verify.isPending}>
            Verify integrity
          </button>
        </div>
      </div>
      {verify.data && (
        <div className={verify.data.valid ? "notice" : "error-note"} role="status">
          {verify.data.valid
            ? `Chain intact — ${verify.data.checked} events verified.`
            : `Tampering detected at event #${verify.data.first_invalid_seq}: ${verify.data.reason}.`}
        </div>
      )}
      <ErrorNote error={verify.error} />
      <Card>
        {events.isLoading && <Spinner />}
        <ErrorNote error={events.error} />
        {events.data?.items.length === 0 && <Empty>No events.</Empty>}
        {events.data && events.data.items.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>When</th>
                  <th>Actor</th>
                  <th>Action</th>
                  <th>Resource</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {events.data.items.map((e) => (
                  <tr key={e.seq}>
                    <td>{e.seq}</td>
                    <td className="small">{formatDate(e.occurred_at)}</td>
                    <td>
                      <Badge value={e.actor_type === "agent" ? "running" : "disabled"} label={e.actor_type} />
                    </td>
                    <td>
                      <code>{e.action}</code>
                    </td>
                    <td className="small">{[e.resource_type, e.resource_id].filter(Boolean).join(": ") || "—"}</td>
                    <td>
                      <Badge value={e.status === "failure" ? "failed" : e.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
