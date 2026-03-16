"use client";

import { authService } from "@/services/auth-service";
import type { User } from "@/types/auth";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  error: string | null;
  loginWithPassword: (email: string, password: string) => Promise<{ success: boolean; error: string | null }>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }): JSX.Element {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refreshUser = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      const nextUser = await authService.getCurrentUser();
      setUser(nextUser);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "认证状态获取失败。");
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshUser();
    const unsubscribe = authService.onAuthStateChange(() => {
      void refreshUser();
    });
    return unsubscribe;
  }, [refreshUser]);

  const loginWithPassword = useCallback(
    async (email: string, password: string): Promise<{ success: boolean; error: string | null }> => {
      const result = await authService.signInWithPassword(email, password);
      if (result.error) {
        return {
          success: false,
          error: result.error
        };
      }

      await refreshUser();
      return {
        success: true,
        error: null
      };
    },
    [refreshUser]
  );

  const logout = useCallback(async (): Promise<void> => {
    await authService.signOut();
    setUser(null);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      loading,
      error,
      loginWithPassword,
      logout,
      refreshUser
    }),
    [user, loading, error, loginWithPassword, logout, refreshUser]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return context;
}
