import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api } from "../../shared/api/client";
import type { KnowledgeDocument, Page, SearchResult } from "../../shared/api/types";
import { useAuth } from "../../shared/auth/AuthContext";
import { Badge, Card, Empty, ErrorNote, Spinner, formatDate } from "../../shared/ui/components";

const ROLES = ["project_manager", "team_lead", "member", "auditor"];

/** Source filters for hybrid search over documents and indexed platform data. */
const SOURCE_FILTERS: { id: string; label: string; sources: string[] }[] = [
  { id: "all", label: "Everything", sources: [] },
  { id: "docs", label: "Documents", sources: ["upload", "text", "demo", "confluence", "notion", "sharepoint", "google_drive"] },
  { id: "work", label: "Jira & code", sources: ["jira", "github", "linear", "gitlab", "azure_devops", "jira_cloud", "youtrack", "clickup", "asana"] },
  { id: "chat", label: "Chat", sources: ["slack", "teams_chat"] },
  { id: "email", label: "Email", sources: ["email"] },
  { id: "ops", label: "Incidents & meetings", sources: ["incidents", "meetings"] },
  { id: "people", label: "People & business", sources: ["keka", "bamboohr", "workday", "zoho_people", "finance", "crm"] },
];

const SOURCE_LABEL: Record<string, string> = {
  upload: "Document", text: "Document", demo: "Document", jira: "Jira", github: "GitHub", slack: "Slack",
  teams_chat: "Teams", email: "Email", incidents: "Ops", meetings: "Meeting", keka: "Keka", finance: "Finance",
  crm: "CRM", linear: "Linear",
};

interface IndexResult {
  project: string;
  sources: { source: string; added: number; updated: number; unchanged: number; removed: number; error: string | null }[];
}

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
  const [filter, setFilter] = useState("all");
  const search = useMutation({
    mutationFn: (q: string) =>
      api<{ results: SearchResult[] }>("/knowledge/search", {
        body: { query: q, top_k: 8, sources: SOURCE_FILTERS.find((f) => f.id === filter)?.sources ?? [] },
      }),
  });
  const index = useMutation({
    mutationFn: () => api<IndexResult[]>("/knowledge/index", { body: {} }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["documents"] }),
  });
  const sync = useMutation({
    mutationFn: () =>
      api<{ source: string; added: number; updated: number; removed: number; error: string | null }[]>("/knowledge/sync", {
        body: {},
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["documents"] }),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/knowledge/documents/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["documents"] }),
  });

  return (
    <>
      <div className="page-header">
        <div>
          <div className="eyebrow">Delivery</div>
          <h1>Knowledge</h1>
          <p>Documents and everything indexed from your connected tools (Jira, GitHub, Slack, email, incidents, meetings, HR).
            Hybrid search: keywords and meaning combined. You only see what you're allowed to read.</p>
        </div>
        {can("knowledge:manage") && (
          <div className="stack" style={{ alignItems: "end" }}>
            <div className="row">
              <button className="button secondary" onClick={() => sync.mutate()} disabled={sync.isPending}>
                {sync.isPending ? "Syncing…" : "Sync document sources"}
              </button>
              <button className="button" onClick={() => index.mutate()} disabled={index.isPending}>
                {index.isPending ? "Indexing…" : "Index project data"}
              </button>
            </div>
            {index.data && (
              <span className="small muted" role="status">
                {index.data.length === 0
                  ? "No active projects to index."
                  : index.data
                      .map((p) => `${p.project}: ${p.sources.reduce((n, s) => n + s.added + s.updated, 0)} new or updated, ` +
                        `${p.sources.reduce((n, s) => n + s.unchanged, 0)} unchanged across ${p.sources.filter((s) => !s.error).length} sources` +
                        (p.sources.some((s) => s.error) ? ` (${p.sources.filter((s) => s.error).map((s) => s.source).join(", ")} unavailable)` : ""))
                      .join(" · ")}
              </span>
            )}
            <ErrorNote error={index.error} />
            {sync.data && (
              <span className="small muted" role="status">
                {sync.data.length === 0
                  ? "No document sources configured."
                  : sync.data
                      .map((r) => (r.error ? `${r.source}: ${r.error}` : `${r.source}: +${r.added} ~${r.updated} -${r.removed}`))
                      .join(" · ")}
              </span>
            )}
            <ErrorNote error={sync.error} />
          </div>
        )}
      </div>
      <Card title="Search">
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            if (query.trim().length >= 2) search.mutate(query.trim());
          }}
        >
          <input style={{ flex: 1 }} aria-label="Search documents" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="e.g. why is PHX-7 blocked, vendor escalation policy" />
          <button className="button" type="submit" disabled={search.isPending}>
            Search
          </button>
        </form>
        <div className="segmented" role="group" aria-label="Search in" style={{ marginTop: 12 }}>
          {SOURCE_FILTERS.map((f) => (
            <button key={f.id} type="button" aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}>
              {f.label}
            </button>
          ))}
        </div>
        <ErrorNote error={search.error} />
        {search.data && search.data.results.length === 0 && <Empty>Nothing you can access matched.</Empty>}
        <div className="stack" style={{ marginTop: 12 }}>
          {search.data?.results.map((r) => (
            <div key={`${r.document_id}-${r.citation}`} className="card" style={{ marginBottom: 0 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="row">
                  {r.source && <Badge value="info" label={SOURCE_LABEL[r.source] ?? r.source} />}
                  <strong>{r.citation}</strong>
                </span>
                <span className="small muted">
                  {[r.project, r.date, r.matched_by.join(" + ")].filter(Boolean).join(" · ")}
                </span>
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
