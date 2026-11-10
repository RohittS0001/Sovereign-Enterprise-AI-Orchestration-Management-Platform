import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../shared/api/client";
import { Card, Empty, ErrorNote, Spinner, formatDate } from "../../shared/ui/components";

interface ApiToken {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
}

const ENDPOINT = `${window.location.origin}/mcp`;

export function McpAccessPage() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("Claude Desktop");
  const [days, setDays] = useState(30);
  const [created, setCreated] = useState<string | null>(null);
  const tokens = useQuery({ queryKey: ["tokens"], queryFn: () => api<ApiToken[]>("/tokens") });
  const create = useMutation({
    mutationFn: () => api<ApiToken & { token: string }>("/tokens", { body: { name, expires_in_days: days } }),
    onSuccess: (token) => {
      setCreated(token.token);
      void queryClient.invalidateQueries({ queryKey: ["tokens"] });
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) => api(`/tokens/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["tokens"] }),
  });

  return (
    <>
      <div className="page-header">
        <div>
          <div className="eyebrow">Platform</div>
          <h1>MCP access</h1>
          <p>
            Let Claude Desktop, an IDE or another agent use the platform's tools through the Model Context Protocol
            (code mode). They act with your permissions; writes still need approval and everything is audited.
          </p>
        </div>
      </div>
      <div className="grid grid-2">
        <Card title="New token">
          <form
            className="stack"
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate();
            }}
          >
            <label>
              Name
              <input value={name} maxLength={100} required onChange={(e) => setName(e.target.value)} />
            </label>
            <label>
              Expires in (days)
              <input type="number" min={1} max={365} value={days} onChange={(e) => setDays(Number(e.target.value))} />
            </label>
            <button className="button" type="submit" disabled={create.isPending}>
              Create token
            </button>
            <ErrorNote error={create.error} />
          </form>
          {created && (
            <div className="notice" role="status">
              <p>Copy it now — it will not be shown again.</p>
              <code style={{ wordBreak: "break-all" }}>{created}</code>
            </div>
          )}
        </Card>
        <Card title="Connect a client">
          <p className="small">HTTP (Streamable HTTP transport):</p>
          <pre>{`URL:    ${ENDPOINT}\nHeader: Authorization: Bearer <token>`}</pre>
          <p className="small">Or a local process (stdio), e.g. in Claude Desktop's config:</p>
          <pre>{`"sovereign": {
  "command": "python",
  "args": ["-m", "scripts.mcp_stdio"],
  "env": { "SOVEREIGN_MCP_TOKEN": "<token>" }
}`}</pre>
        </Card>
      </div>
      <Card title={`Your tokens (${tokens.data?.length ?? 0})`}>
        {tokens.isLoading && <Spinner />}
        <ErrorNote error={tokens.error} />
        {tokens.data?.length === 0 && <Empty>No active tokens.</Empty>}
        <ul style={{ listStyle: "none", padding: 0 }}>
          {tokens.data?.map((t) => (
            <li key={t.id} className="row" style={{ justifyContent: "space-between", padding: "6px 0" }}>
              <span>
                <strong>{t.name}</strong> <code className="small">{t.prefix}…</code>
                <span className="small muted">
                  {" "}
                  created {formatDate(t.created_at)} · expires {formatDate(t.expires_at)} · last used{" "}
                  {t.last_used_at ? formatDate(t.last_used_at) : "never"}
                </span>
              </span>
              <button className="button ghost small" onClick={() => revoke.mutate(t.id)} disabled={revoke.isPending}>
                Revoke
              </button>
            </li>
          ))}
        </ul>
      </Card>
    </>
  );
}
