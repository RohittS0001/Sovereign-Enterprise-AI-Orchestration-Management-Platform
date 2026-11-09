import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import { Layout } from "./components/Layout";
import { Spinner } from "./components/ui";
import { AdminPage } from "./pages/Admin";
import { ApprovalsPage } from "./pages/Approvals";
import { AssistantPage } from "./pages/Assistant";
import { AuditPage } from "./pages/Audit";
import { KnowledgePage } from "./pages/Knowledge";
import { LoginPage } from "./pages/Login";
import { ProjectHealthPage, ProjectsPage } from "./pages/Projects";
import { ReportsPage } from "./pages/Reports";

function Guard({ permission, children }: { permission?: string; children: JSX.Element }) {
  const { can } = useAuth();
  if (permission && !can(permission)) {
    return <p className="empty">You don't have access to this page.</p>;
  }
  return children;
}

export function App() {
  const { user, loading } = useAuth();
  if (loading) return <Spinner label="Starting…" />;
  if (!user) return <LoginPage />;

  const home = user.permissions.includes("agent:query") ? "/assistant" : "/projects";
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to={home} replace />} />
        <Route path="assistant" element={<Guard permission="agent:query"><AssistantPage /></Guard>} />
        <Route path="approvals" element={<ApprovalsPage />} />
        <Route path="projects" element={<Guard permission="projects:read"><ProjectsPage /></Guard>} />
        <Route path="projects/:key" element={<Guard permission="analytics:read"><ProjectHealthPage /></Guard>} />
        <Route path="reports" element={<Guard permission="reports:read"><ReportsPage /></Guard>} />
        <Route path="knowledge" element={<Guard permission="knowledge:read"><KnowledgePage /></Guard>} />
        <Route path="audit" element={<Guard permission="audit:read"><AuditPage /></Guard>} />
        <Route path="admin" element={<Guard permission="users:manage"><AdminPage /></Guard>} />
        <Route path="*" element={<Navigate to={home} replace />} />
      </Route>
    </Routes>
  );
}
