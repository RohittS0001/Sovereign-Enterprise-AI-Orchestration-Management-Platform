// Shapes returned by the FastAPI backend (see backend/app/schemas).

export type Role = "admin" | "project_manager" | "team_lead" | "member" | "auditor";

export interface CurrentUser {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  permissions: string[];
  agent_permissions: string[];
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  expires_in: number;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface AgentStep {
  id: string;
  tool: string;
  purpose: string;
  status: "success" | "error" | "denied" | "pending_approval" | string;
  error: string | null;
  approval_id: string | null;
  suspicious: boolean;
}

export interface AgentRun {
  run_id: string;
  status: "running" | "completed" | "awaiting_approval" | "failed";
  query: string;
  answer: string | null;
  citations: string[];
  steps: AgentStep[];
  pending_approvals: string[];
  flagged_injection: boolean;
  error: string | null;
  usage: { prompt_tokens: number; completion_tokens: number };
  started_at: string;
  finished_at: string | null;
}

export interface Approval {
  id: string;
  run_id: string | null;
  requested_by_id: string;
  tool_name: string;
  permission: string;
  arguments: Record<string, unknown>;
  summary: string;
  risk_flags: string[];
  status: "pending" | "rejected" | "expired" | "executed" | "failed";
  created_at: string;
  expires_at: string;
  decided_by_id: string | null;
  decided_at: string | null;
  decision_note: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
}

export interface RiskFactor {
  name: string;
  score: number;
  max_score: number;
  evidence: string[];
  recommendation: string | null;
}

export interface MemberLoad {
  name: string;
  open_issues: string[];
  open_points: number;
  capacity_points: number;
  availability: number;
  effective_capacity: number;
  load_ratio: number;
  status: "overloaded" | "balanced" | "underused" | "unknown";
  leave_days: number;
  at_risk_issues: string[];
}

export interface ProjectHealth {
  project: string;
  name: string;
  as_of: string;
  risk: { score: number; level: "low" | "medium" | "high"; factors: RiskFactor[] };
  /** Open CRM deals tagged with this project key. */
  commitments?: { source: string; id: string; name: string; stage?: string; amount?: number; close_date?: string; url?: string }[] | null;
  /** Earned value when a finance connector covers this project. */
  finance?: {
    sources: string[];
    currency: string | null;
    budget_at_completion: number | null;
    actual_cost: number | null;
    percent_complete: number | null;
    earned_value: number | null;
    cpi: number | null;
    estimate_at_completion: number | null;
    variance_at_completion: number | null;
    note: string | null;
    /** Per currency; earned value is never computed across currencies. */
    by_currency?: Record<string, { budget: number | null; actual: number | null }>;
  } | null;
  /** Present when ops connectors cover this project (see project links). */
  production?: {
    incidents: { source: string; id: string; title: string; status: string; severity?: string; url?: string }[];
    errors: { source: string; id: string; title: string; events: number; users: number; url?: string }[];
    deployments: { source: string; name: string; status: string; detail?: string }[];
    quality_gates: { source: string; project: string; status: string; failing: string[] }[];
  } | null;
  forecast: {
    sprint: string;
    end: string | null;
    committed_points: number;
    done_points: number;
    projected_points: number;
    completion_ratio: number;
    burn_rate_per_day: number;
    working_days_left: number;
    status: "on_track" | "at_risk" | "off_track" | "unknown";
    remaining_issues: string[];
    note: string | null;
  } | null;
  workload: {
    period_end: string;
    members: MemberLoad[];
    unassigned_open: string[];
    suggestions: { issue: string; points: number; from_member: string; to_member: string; reason: string }[];
  };
  contradictions: { kind: string; issue: string; detail: string; evidence: string[] }[];
  issue_counts: Record<string, number>;
  warnings: string[];
}

export interface Project {
  id: string;
  key: string;
  name: string;
  github_repos: string[];
  active_sprint_id: number | null;
  members: { name: string }[];
  report_recipients: string[];
  is_active: boolean;
}

export interface ReportSummary {
  id: string;
  project_key: string;
  kind: string;
  trigger: string;
  period_start: string;
  period_end: string;
  risk_score: number;
  risk_level: string;
  generated_by_model: string | null;
  created_at: string;
}

export interface Report extends ReportSummary {
  content_markdown: string;
}

export interface KnowledgeDocument {
  id: string;
  title: string;
  source_type: string;
  filename: string | null;
  visibility: "organization" | "roles" | "private";
  allowed_roles: string[];
  tags: string[];
  char_count: number;
  chunk_count: number;
  created_at: string;
}

export interface SearchResult {
  document_id: string;
  title: string;
  section: string | null;
  citation: string;
  score: number;
  matched_by: string[];
  source?: string | null;
  project?: string | null;
  date?: string | null;
  text: string;
}

export interface AuditEvent {
  seq: number;
  occurred_at: string;
  actor_type: string;
  actor_user_id: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  status: string;
  details: Record<string, unknown>;
}

export interface UserRow {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface IntegrationStatus {
  name: string;
  title: string;
  category: string;
  mode: "live" | "demo" | "disabled";
  tools: string[];
  /** Env vars still needed to enable it (empty once configured). */
  missing_env: string[];
  setup: string;
  docs_url: string;
  healthy: boolean | null;
  detail: string | null;
}

export interface UsageSummary {
  runs: number;
  tokens: number;
  by_user: {
    user_id: string;
    email: string | null;
    runs: number;
    failed_runs: number;
    tool_calls: number;
    prompt_tokens: number;
    completion_tokens: number;
  }[];
  by_day: { day: string; runs: number; tokens: number }[];
}
