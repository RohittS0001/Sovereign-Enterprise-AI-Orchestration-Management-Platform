import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api } from "../../shared/api/client";
import type { KnowledgeDocument, Page, SearchResult } from "../../shared/api/types";
import { useAuth } from "../../shared/auth/AuthContext";
import { Badge, Card, Empty, ErrorNote, Spinner, formatDate } from "../../shared/ui/components";

const ROLES = ["project_manager", "team_lead", "member", "auditor"];

function UploadForm() {
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [visibility, setVisibility] = useState("organization");
  const [roles, setRoles] = useState<string[]>([]);
  const upload = useMutation({
    mutationFn: () => {
      const form = new FormData();
      form.append("file", file!);
      form.append("visibility", visibility);
      form.append("allowed_roles", JSON.stringify(visibility === "roles" ? roles : []));
      return api<KnowledgeDocument>("/knowledge/documents", { form });
    },
    onSuccess: () => {
      setFile(null);
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (file) upload.mutate();
  }

  return (
    <Card title="Add a document">
      <form className="stack" onSubmit={submit}>
        <label>
          File (.pdf, .docx, .md, .txt, .html)
          <input type="file" accept=".pdf,.docx,.md,.markdown,.txt,.html,.htm" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        <label>
          Who can read it
          <select value={visibility} onChange={(e) => setVisibility(e.target.value)}>
            <option value="organization">Everyone</option>
            <option value="roles">Selected roles</option>
            <option value="private">Only me (and admins)</option>
          </select>
        </label>
        {visibility === "roles" && (
          <div className="row small">
            {ROLES.map((role) => (
              <label key={role} style={{ flexDirection: "row", alignItems: "center" }}>
                <input
                  type="checkbox"
                  checked={roles.includes(role)}
                  onChange={(e) => setRoles((r) => (e.target.checked ? [...r, role] : r.filter((x) => x !== role)))}
                />
                {role.replace("_", " ")}
              </label>
            ))}
          </div>
        )}
        <div>
          <button className="button" type="submit" disabled={!file || upload.isPending}>
            {upload.isPending ? "Uploading…" : "Upload"}
          </button>
        </div>
        {upload.isSuccess && <div className="notice">Added “{upload.data.title}” ({upload.data.chunk_count} passages).</div>}
        <ErrorNote error={upload.error} />
      </form>
    </Card>
  );
}

export function KnowledgePage() {
  const { can, user } = useAuth();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const documents = useQuery({ queryKey: ["documents"], queryFn: () => api<Page<KnowledgeDocument>>("/knowledge/documents") });
  const search = useMutation({
    mutationFn: (q: string) => api<{ results: SearchResult[] }>("/knowledge/search", { body: { query: q, top_k: 8 } }),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/knowledge/documents/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["documents"] }),
  });

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Knowledge</h1>
          <p>Internal documents the assistant can cite. You only see what you're allowed to read.</p>
        </div>
      </div>
      <Card title="Search">
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            if (query.trim().length >= 2) search.mutate(query.trim());
          }}
        >
          <input style={{ flex: 1 }} aria-label="Search documents" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="e.g. vendor escalation" />
          <button className="button" type="submit" disabled={search.isPending}>
            Search
          </button>
        </form>
        <ErrorNote error={search.error} />
        {search.data && search.data.results.length === 0 && <Empty>No accessible documents matched.</Empty>}
        <div className="stack" style={{ marginTop: 12 }}>
          {search.data?.results.map((r) => (
            <div key={`${r.document_id}-${r.citation}`} className="card" style={{ marginBottom: 0 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <strong>{r.citation}</strong>
                <span className="small muted">{r.matched_by.join(" + ")}</span>
              </div>
              <p className="small" style={{ whiteSpace: "pre-wrap" }}>{r.text}</p>
            </div>
          ))}
        </div>
      </Card>
      <div className="grid grid-2">
        <Card title={`Documents (${documents.data?.total ?? 0})`}>
          {documents.isLoading && <Spinner />}
          <ErrorNote error={documents.error} />
          {documents.data?.items.length === 0 && <Empty>No documents yet.</Empty>}
          <ul style={{ paddingLeft: 18 }}>
            {documents.data?.items.map((d) => (
              <li key={d.id} style={{ marginBottom: 8 }}>
                <strong>{d.title}</strong> <Badge value={d.visibility === "organization" ? "live" : "demo"} label={d.visibility} />
                <div className="small muted">
                  {d.chunk_count} passages · {formatDate(d.created_at)}
                  {d.allowed_roles.length > 0 && ` · roles: ${d.allowed_roles.join(", ")}`}
                </div>
                {(user?.role === "admin" || can("knowledge:manage")) && (
                  <button className="button ghost small" onClick={() => remove.mutate(d.id)} disabled={remove.isPending}>
                    Delete
                  </button>
                )}
              </li>
            ))}
          </ul>
          <ErrorNote error={remove.error} />
        </Card>
        {can("knowledge:manage") && <UploadForm />}
      </div>
    </>
  );
}
