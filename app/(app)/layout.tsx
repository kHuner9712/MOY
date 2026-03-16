"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";

import { useAuth } from "@/components/auth/auth-provider";
import { AppShell } from "@/components/layout/app-shell";
import { canAccessPath } from "@/lib/auth";

export default function WorkbenchLayout({ children }: { children: React.ReactNode }): JSX.Element {
  const { user, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (loading) return;
    if (!user) {
      router.replace("/login");
      return;
    }

    if (!canAccessPath(user.role, pathname)) {
      router.replace("/dashboard");
    }
  }, [loading, user, router, pathname]);

  if (loading || !user) {
    return <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">正在检查登录状态...</div>;
  }

  if (!canAccessPath(user.role, pathname)) {
    return <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">正在跳转到可访问页面...</div>;
  }

  return <AppShell>{children}</AppShell>;
}
