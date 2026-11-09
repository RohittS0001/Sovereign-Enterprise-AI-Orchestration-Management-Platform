import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

const NAV = [
  { to: "/assistant", label: "Assistant", permission: "agent:query" },
  { to: "/approvals", label: "Approvals", permission: null },
  { to: "/projects", label: "Projects", permission: "projects:read" },
  { to: "/reports", label: "Reports", permission: "reports:read" },
  { to: "/knowledge", label: "Knowledge", permission: "knowledge:read" },
  { to: "/audit", label: "Audit log", permission: "audit:read" },
  { to: "/admin", label: "Admin", permission: "users:manage" },
];

export function Layout() {
  const { user, logout, can } = useAuth();
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden>◆</span> Sovereign
        </div>
        <nav aria-label="Main">
          {NAV.filter((item) => !item.permission || can(item.permission)).map((item) => (
            <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="who">
            <strong>{user?.full_name}</strong>
            <span>{user?.role.replace("_", " ")}</span>
          </div>
          <button className="button ghost" onClick={() => void logout()}>
            Sign out
          </button>
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
