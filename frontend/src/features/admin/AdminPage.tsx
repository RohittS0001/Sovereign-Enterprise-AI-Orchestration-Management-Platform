import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api } from "../../shared/api/client";
import type { Page, Role, UsageSummary, UserRow } from "../../shared/api/types";
import { useAuth } from "../../shared/auth/AuthContext";
import { Badge, Card, ErrorNote, PageHeader, Spinner, formatDate } from "../../shared/ui/components";

const ROLES: Role[] = ["admin", "project_manager", "team_lead", "member", "auditor"];

function Users() {
  const queryClient = useQueryClient();
  const { user: me } = useAuth();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<Page<UserRow>>("/users?limit=200") });
  const [form, setForm] = useState({ email: "", full_name: "", password: "", role: "member" as Role });
  const create = useMutation({
    mutationFn: () => api<UserRow>("/users", { body: form }),
    onSuccess: () => {
      setForm({ email: "", full_name: "", password: "", role: "member" });
      void queryClient.invalidateQueries({ queryKey: ["users"] });
    },
  });
  const update = useMutation({
    mutationFn: ({ id, changes }: { id: string; changes: Partial<UserRow> }) =>
      api<UserRow>(`/users/${id}`, { method: "PATCH", body: changes }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["users"] }),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  return (
    <Card title="Users">
      <form className="row" onSubmit={submit} style={{ marginBottom: 12, alignItems: "flex-end" }}>
        <label>
          Email
          <input type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        </label>
        <label>
          Name
          <input required value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
        </label>
        <label>
          Initial password
          <input type="password" required minLength={12} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        </label>
        <label>
          Role
          <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r.replace("_", " ")}
              </option>
            ))}
          </select>
        </label>
        <button className="button" type="submit" disabled={create.isPending}>
          Add user
        </button>
      </form>
      <ErrorNote error={create.error} />
      <ErrorNote error={update.error} />
      {users.isLoading && <Spinner />}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>User</th>
              <th>Role</th>
              <th>Last login</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {users.data?.items.map((u) => (
              <tr key={u.id}>
                <td>
                  {u.full_name}
                  <div className="small muted">{u.email}</div>
                </td>
                <td>
                  <select
                    aria-label={`Role for ${u.email}`}
                    value={u.role}
                    disabled={u.id === me?.id}
                    onChange={(e) => update.mutate({ id: u.id, changes: { role: e.target.value as Role } })}
                  >
                    {ROLES.map((r) => (
                      <option key={r} value={r}>
                        {r.replace("_", " ")}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="small">{formatDate(u.last_login_at)}</td>
                <td>
                  <button
                    className="button ghost small"
                    disabled={u.id === me?.id}
                    onClick={() => update.mutate({ id: u.id, changes: { is_active: !u.is_active } })}
                  >
                    {u.is_active ? "Deactivate" : "Reactivate"}
                  </button>{" "}
                  <Badge value={u.is_active ? "live" : "disabled"} label={u.is_active ? "active" : "inactive"} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Usage() {
  const usage = useQuery({ queryKey: ["usage"], queryFn: () => api<UsageSummary>("/usage?days=30") });
  if (usage.isLoading) return <Spinner />;
  if (!usage.data) return <ErrorNote error={usage.error} />;
  const u = usage.data;
  return (
    <Card title="AI usage (30 days)">
      <div className="row" style={{ gap: 32, marginBottom: 12 }}>
        <div>
          <div className="stat">{u.runs}</div>
          <div className="small muted">questions</div>
        </div>
        <div>
          <div className="stat">{u.tokens.toLocaleString()}</div>
          <div className="small muted">tokens</div>
        </div>
      </div>
      <table>
        <thead>
          <tr>
            <th>User</th>
            <th>Runs</th>
            <th>Tool calls</th>
            <th>Tokens</th>
          </tr>
        </thead>
        <tbody>
          {u.by_user.map((row) => (
            <tr key={row.user_id}>
              <td>{row.email ?? row.user_id}</td>
              <td>
                {row.runs}
                {row.failed_runs > 0 && <span className="small muted"> ({row.failed_runs} failed)</span>}
              </td>
              <td>{row.tool_calls}</td>
              <td>{(row.prompt_tokens + row.completion_tokens).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

export function AdminPage() {
  return (
    <>
      <PageHeader eyebrow="Platform" title="Admin" description="People, roles and AI usage. Integrations have their own page." />
      <Usage />
      <Users />
    </>
  );
}
