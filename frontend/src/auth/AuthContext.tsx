import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, getRefreshToken, refreshSession, setSessionExpiredHandler, storeTokens } from "../api/client";
import type { CurrentUser, TokenPair } from "../api/types";

interface AuthState {
  user: CurrentUser | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  can: (permission: string) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);

  const clear = useCallback(() => {
    storeTokens(null);
    setUser(null);
  }, []);

  useEffect(() => {
    setSessionExpiredHandler(clear);
    // Resume a session after a page reload using the stored refresh token.
    (async () => {
      try {
        if (getRefreshToken() && (await refreshSession())) {
          setUser(await api<CurrentUser>("/auth/me"));
        }
      } catch {
        clear();
      } finally {
        setLoading(false);
      }
    })();
  }, [clear]);

  const login = useCallback(async (email: string, password: string) => {
    const tokens = await api<TokenPair>("/auth/login", { body: { email, password } });
    storeTokens(tokens);
    setUser(await api<CurrentUser>("/auth/me"));
  }, []);

  const logout = useCallback(async () => {
    const token = getRefreshToken();
    try {
      if (token) await api("/auth/logout", { body: { refresh_token: token } });
    } finally {
      clear();
    }
  }, [clear]);

  const value = useMemo<AuthState>(
    () => ({
      user,
      loading,
      login,
      logout,
      can: (permission) => user?.permissions.includes(permission) ?? false,
    }),
    [user, loading, login, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
