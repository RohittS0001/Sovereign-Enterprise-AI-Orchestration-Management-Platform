import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "../../shared/api/client";
import type { IntegrationStatus } from "../../shared/api/types";
import { Badge, ErrorNote, Kpi, PageHeader, Spinner } from "../../shared/ui/components";
import { Icon } from "../../shared/ui/icons";

const CATEGORY_LABELS: Record<string, string> = {
  core: "Core", tracker: "Trackers", ops: "Engineering ops", hr: "HR", finance: "Finance", crm: "CRM",
  docs: "Documents", calendar: "Calendars", meetings: "Meetings", chat: "Chat",
};

/** Every integration: connected or not, its tools, and exactly what to put in connections.env. */
export function IntegrationsPage() {
  const [check, setCheck] = useState(false);
  const [category, setCategory] = useState("all");
  const [query, setQuery] = useState("");
  const status = useQuery({
    queryKey: ["integrations", check],
    queryFn: () => api<IntegrationStatus[]>(`/integrations${check ? "?check=true" : ""}`),
  });
  const items = status.data ?? [];
  const categories = useMemo(() => [...new Set(items.map((i) => i.category))], [items]);
  const visible = items.filter(
    (i) =>
      (category === "all" || i.category === category) &&
      (!query || `${i.title} ${i.name}`.toLowerCase().includes(query.toLowerCase())),
  );
  const connected = items.filter((i) => i.mode !== "disabled").length;
  const failing = items.filter((i) => i.healthy === false).length;

  return (
    <>
      <PageHeader
        eyebrow="Platform"
        title="Integrations"
        description={
          <>
            Every link and key lives in one file, <code>connections.env</code> (create it with{" "}
            <code>python -m scripts.connections --init</code>). Paste the listed values and restart to connect more;{" "}
            <code>python -m scripts.connections</code> prints the same checklist in the terminal.
          </>
        }
        actions={
          <button className="button secondary" onClick={() => setCheck(true)} disabled={status.isFetching}>
            <Icon name="refresh" /> {status.isFetching && check ? "Testing…" : "Test connections"}
          </button>
        }
      />
      <div className="grid grid-3">
        <Kpi icon="plug" label="Connected" value={`${connected} / ${items.length || "…"}`}
             foot={status.data ? `${connected} of ${items.length} connected` : undefined} />
        <Kpi icon="activity" label="Tools available to the agent"
             value={items.reduce((n, i) => n + i.tools.length, 0)} foot="Across all connected systems" />
        <Kpi icon="alert" label="Failing health checks" value={check ? failing : "—"} tone={failing ? "bad" : undefined}
             foot={check ? "Live check just ran" : "Run “Test connections”"} />
      </div>
      <div className="row between" style={{ marginBottom: 16 }}>
        <div className="segmented" role="group" aria-label="Category">
          {["all", ...categories].map((c) => (
            <button key={c} type="button" aria-pressed={category === c} onClick={() => setCategory(c)}>
              {c === "all" ? "All" : CATEGORY_LABELS[c] ?? c}
            </button>
          ))}
        </div>
        <input aria-label="Filter integrations" placeholder="Filter…" value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>
      {status.isLoading && <Spinner />}
      <ErrorNote error={status.error} />
      <ul className="integration-grid">
        {visible.map((i) => (
          <li key={i.name} className="card">
            <div className="row between">
              <strong>{i.title}</strong>
              <span className="row">
                {i.healthy !== null && (
                  <Badge value={i.healthy ? "healthy" : "failed"} label={i.healthy ? "ok" : i.detail ?? "failed"} />
                )}
                <Badge value={i.mode} label={i.mode === "disabled" ? "not connected" : i.mode} />
              </span>
            </div>
            <div className="xs faint">{CATEGORY_LABELS[i.category] ?? i.category} · {i.tools.length} tools</div>
            {i.missing_env.length > 0 && (
              <div className="small">
                <div className="muted" style={{ margin: "8px 0 4px" }}>Needs</div>
                <div className="row">
                  {i.missing_env.map((v) => (
                    <code key={v}>{v}</code>
                  ))}
                </div>
                {i.setup && <p className="xs muted">{i.setup}</p>}
              </div>
            )}
            {i.docs_url && (
              <a className="xs" href={i.docs_url} target="_blank" rel="noreferrer">
                API docs
              </a>
            )}
          </li>
        ))}
      </ul>
    </>
  );
}
