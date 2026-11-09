import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../shared/api/client";
import type { Approval, Page } from "../../shared/api/types";
import { useAuth } from "../../shared/auth/AuthContext";
import { Badge, Card, Empty, ErrorNote, Spinner, formatDate } from "../../shared/ui/components";

const FILTERS = ["pending", "executed", "rejected", "failed", "expired"] as const;

function ApprovalCard({ approval, canDecide }: { approval: Approval; canDecide: boolean }) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const decide = useMutation({
    mutationFn: (decision: "approve" | "reject") =>
      api<Approval>(`/approvals/${approval.id}/${decision}`, { body: { note: note || null } }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["approvals"] }),
  });

  return (
    <Card title={<code>{approval.tool_name}</code>} actions={<Badge value={approval.status} />}>
      <div className="stack">
        {approval.risk_flags.length > 0 && (
          <div className="notice">
            Flagged: {approval.risk_flags.join(", ").replace(/_/g, " ")}. The request may have been influenced by
            untrusted content — check it carefully.
          </div>
        )}
        <pre>{JSON.stringify(approval.arguments, null, 2)}</pre>
        <div className="small muted">
          Requested {formatDate(approval.created_at)} · expires {formatDate(approval.expires_at)}
        </div>
        {approval.decision_note && <div className="small">Note: {approval.decision_note}</div>}
        {approval.error && <ErrorNote error={new Error(approval.error)} />}
        {approval.result && <pre>{JSON.stringify(approval.result, null, 2)}</pre>}
        {approval.status === "pending" && canDecide && (
          <>
            <label>
              Note (optional)
              <input value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
            </label>
            <div className="row">
              <button className="button" disabled={decide.isPending} onClick={() => decide.mutate("approve")}>
                Approve and run
              </button>
              <button className="button secondary" disabled={decide.isPending} onClick={() => decide.mutate("reject")}>
                Reject
              </button>
            </div>
            <ErrorNote error={decide.error} />
          </>
        )}
      </div>
    </Card>
  );
}

export function ApprovalsPage() {
  const { can } = useAuth();
  const [status, setStatus] = useState<(typeof FILTERS)[number]>("pending");
  const approvals = useQuery({
    queryKey: ["approvals", status],
    queryFn: () => api<Page<Approval>>(`/approvals?status=${status}`),
    refetchInterval: status === "pending" ? 15_000 : false,
  });

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Approvals</h1>
          <p>
            {can("approvals:decide")
              ? "Actions proposed by the assistant. Nothing changes until someone approves it."
              : "Actions the assistant proposed for you. An approver will review them."}
          </p>
        </div>
        <label>
          Show
          <select value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
            {FILTERS.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </label>
      </div>
      {approvals.isLoading && <Spinner />}
      <ErrorNote error={approvals.error} />
      {approvals.data?.items.length === 0 && <Empty>Nothing here.</Empty>}
      {approvals.data?.items.map((approval) => (
        <ApprovalCard key={approval.id} approval={approval} canDecide={can("approvals:decide")} />
      ))}
    </>
  );
}
