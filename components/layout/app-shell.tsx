"use client";

import { useAuth } from "@/components/auth/auth-provider";
import { AppHeader } from "@/components/layout/app-header";
import { AppSidebar } from "@/components/layout/app-sidebar";
import { BottomTabBar } from "@/components/mobile/BottomTabBar";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { usePathname } from "next/navigation";
import { useState } from "react";

export function AppShell({ children }: { children: React.ReactNode }): JSX.Element {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const { user } = useAuth();

  if (!user) return <>{children}</>;

  return (
    <div className="min-h-screen bg-transparent">
      <div className="hidden lg:fixed lg:inset-y-0 lg:block">
        <AppSidebar role={user.role} pathname={pathname} />
      </div>

      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent side="left" className="w-64 p-0">
          <AppSidebar role={user.role} pathname={pathname} onNavigate={() => setMobileOpen(false)} />
        </SheetContent>
      </Sheet>

      <div className="lg:pl-64">
        <AppHeader onOpenNav={() => setMobileOpen(true)} />
        <main className="p-4 pb-24 lg:p-6 lg:pb-6">{children}</main>
        <BottomTabBar />
      </div>
    </div>
  );
}
