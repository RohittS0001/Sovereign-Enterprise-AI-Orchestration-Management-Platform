import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Approval, Page } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Avatar } from "./components";
import { Icon, type IconName } from "./icons";
import { useTheme } from "./theme";

export interface NavItem {
  to: string;
  label: string;
  icon: IconName;
  permission: string | null;
  section: string;
}

export const NAV: NavItem[] = [
  { to: "/home", label: "Home", icon: "home", permission: "analytics:read", section: "Workspace" },
  { to: "/assistant", label: "Assistant", icon: "sparkles", permission: "agent:query", section: "Workspace" },
  { to: "/approvals", label: "Approvals", icon: "check", permission: null, section: "Workspace" },
  { to: "/projects", label: "Projects", icon: "folder", permission: "projects:read", section: "Delivery" },
  { to: "/reports", label: "Reports", icon: "report", permission: "reports:read", section: "Delivery" },
  { to: "/knowledge", label: "Knowledge", icon: "book", permission: "knowledge:read", section: "Delivery" },
  { to: "/people", label: "People", icon: "network", permission: "people:read", section: "Insights" },
  { to: "/compliance", label: "Compliance", icon: "shield", permission: "audit:read", section: "Insights" },
  { to: "/integrations", label: "Integrations", icon: "plug", permission: "users:manage", section: "Platform" },
  { to: "/mcp-access", label: "MCP access", icon: "key", permission: "agent:query", section: "Platform" },
  { to: "/audit", label: "Audit log", icon: "activity", permission: "audit:read", section: "Platform" },
  { to: "/admin", label: "Admin", icon: "settings", permission: "users:manage", section: "Platform" },
];

function CommandPalette({ items, onClose, onToggleTheme, onSignOut }: {
  items: NavItem[]; onClose: () => void; onToggleTheme: () => void; onSignOut: () => void;
}) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const commands = useMemo(() => {
    const all = [
      ...items.map((i) => ({ id: i.to, label: `Go to ${i.label}`, icon: i.icon, run: () => navigate(i.to) })),
      { id: "theme", label: "Toggle light / dark theme", icon: "moon" as IconName, run: onToggleTheme },
      { id: "signout", label: "Sign out", icon: "logout" as IconName, run: onSignOut },
    ];
    const q = query.trim().toLowerCase();
    return q ? all.filter((c) => c.label.toLowerCase().includes(q)) : all;
  }, [items, query, navigate, onToggleTheme, onSignOut]);

  useEffect(() => input.current?.focus(), []);
  useEffect(() => setIndex(0), [query]);

  const run = (i: number) => {
    const command = commands[i];
    if (command) {
      onClose();
      command.run();
    }
  };

  return (
    <div className="overlay" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="palette" role="dialog" aria-modal="true" aria-label="Command palette">
        <input
          ref={input}
          aria-label="Search commands"
          placeholder="Search pages and actions…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") onClose();
            if (e.key === "ArrowDown") { e.preventDefault(); setIndex((i) => Math.min(i + 1, commands.length - 1)); }
            if (e.key === "ArrowUp") { e.preventDefault(); setIndex((i) => Math.max(i - 1, 0)); }
            if (e.key === "Enter") run(index);
          }}
        />
        <ul role="listbox" aria-label="Commands">
          {commands.map((c, i) => (
            <li key={c.id}>
              <button type="button" role="option" aria-selected={i === index} onMouseEnter={() => setIndex(i)} onClick={() => run(i)}>
                <Icon name={c.icon} /> {c.label}
              </button>
            </li>
          ))}
          {commands.length === 0 && <li className="small muted" style={{ padding: 12 }}>No matches</li>}
        </ul>
      </div>
    </div>
  );
}

export function Layout() {
  const { user, logout, can } = useAuth();
  const [theme, toggleTheme] = useTheme();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const location = useLocation();
  const items = NAV.filter((item) => !item.permission || can(item.permission));
  const pending = useQuery({
    queryKey: ["approvals", "pending-count"],
    queryFn: () => api<Page<Approval>>("/approvals?status=pending&limit=1"),
    refetchInterval: 30_000,
    enabled: can("approvals:decide"),
  });

  useEffect(() => setNavOpen(false), [location.pathname]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const sections = [...new Set(items.map((i) => i.section))];
  return (
    <div className={`shell ${navOpen ? "nav-open" : ""}`}>
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden>◆</span>
          <div>
            Sovereign
            <small>AI delivery platform</small>
          </div>
        </div>
        <nav aria-label="Main">
          {sections.map((section) => (
            <div key={section}>
              <div className="nav-section">{section}</div>
              {items.filter((i) => i.section === section).map((item) => (
                <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}>
                  <Icon name={item.icon} />
                  {item.label}
                  {item.to === "/approvals" && (pending.data?.total ?? 0) > 0 && (
                    <span className="nav-count" aria-label={`${pending.data?.total} pending`}>{pending.data?.total}</span>
                  )}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="who">
            <Avatar name={user?.full_name} />
            <div>
              <strong>{user?.full_name}</strong>
              <span>{user?.role.replace("_", " ")}</span>
            </div>
          </div>
          <button className="button ghost" style={{ justifyContent: "flex-start" }} onClick={() => void logout()}>
            <Icon name="logout" /> Sign out
          </button>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <button className="icon-button menu-toggle" aria-label="Open navigation" onClick={() => setNavOpen((o) => !o)}>
            <Icon name="menu" />
          </button>
          <button className="search-trigger" onClick={() => setPaletteOpen(true)} aria-label="Search (Ctrl K)">
            <Icon name="search" /> <span>Search or jump to…</span> <kbd>Ctrl K</kbd>
          </button>
          <div className="topbar-actions">
            <button className="icon-button" onClick={toggleTheme} aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}>
              <Icon name={theme === "dark" ? "sun" : "moon"} />
            </button>
          </div>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
      {paletteOpen && (
        <CommandPalette items={items} onClose={() => setPaletteOpen(false)} onToggleTheme={toggleTheme}
                        onSignOut={() => void logout()} />
      )}
    </div>
  );
}
