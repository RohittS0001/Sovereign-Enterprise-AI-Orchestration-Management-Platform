import { useState, type FormEvent } from "react";
import { useAuth } from "../../shared/auth/AuthContext";
import { ErrorNote } from "../../shared/ui/components";

export function LoginPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <aside className="login-hero" aria-hidden="true">
        <div className="brand" style={{ padding: 0, color: "#fff" }}>
          <span className="brand-mark">◆</span> Sovereign
        </div>
        <div>
          <h2>Your delivery organization, with an AI chief of staff.</h2>
          <ul>
            <li>Agent over Jira, GitHub, HR, finance and 30+ more systems</li>
            <li>Monte Carlo forecasts, risk scores and decisions to take</li>
            <li>Every write approved by a person, every step audited</li>
            <li>Runs on models you host — your data stays yours</li>
          </ul>
        </div>
        <p className="small" style={{ color: "#a5b4fc", margin: 0 }}>Self-hosted · Role-based access · Hash-chained audit log</p>
      </aside>
      <div className="login-panel">
        <form onSubmit={submit}>
          <div>
            <h1>Sign in</h1>
            <p className="muted small" style={{ margin: "4px 0 0" }}>Welcome back to Sovereign.</p>
          </div>
          <label>
            Email
            <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </label>
          <label>
            Password
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          <ErrorNote error={error} />
          <button className="button" type="submit" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
