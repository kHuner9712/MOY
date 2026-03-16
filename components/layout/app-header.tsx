"use client";

import { useEffect, useState } from "react";
import { Menu, MoonStar, SunMedium } from "lucide-react";

import { useAuth } from "@/components/auth/auth-provider";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { getInitials } from "@/lib/utils";

interface AppHeaderProps {
  onOpenNav: () => void;
}

export function AppHeader({ onOpenNav }: AppHeaderProps): JSX.Element {
  const { user, logout } = useAuth();
  const [darkMode, setDarkMode] = useState(false);

  useEffect(() => {
    const isDark = window.document.documentElement.classList.contains("dark");
    setDarkMode(isDark);
  }, []);

  const toggleTheme = (): void => {
    const nextDark = !darkMode;
    setDarkMode(nextDark);
    window.document.documentElement.classList.toggle("dark", nextDark);
  };

  return (
    <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b bg-white/90 px-4 backdrop-blur-sm lg:px-6">
      <div className="flex items-center gap-3">
        <Button size="icon" variant="ghost" className="lg:hidden" onClick={onOpenNav}>
          <Menu className="h-5 w-5" />
        </Button>
        <div>
          <p className="text-xs uppercase tracking-[0.16em] text-slate-500">MOY Workbench</p>
          <p className="text-sm font-semibold text-slate-900">销售团队 AI 推进系统</p>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <Button variant="outline" size="icon" onClick={toggleTheme} title="切换主题">
          {darkMode ? <SunMedium className="h-4 w-4" /> : <MoonStar className="h-4 w-4" />}
        </Button>
        <Separator orientation="vertical" className="hidden h-6 sm:block" />
        <div className="hidden text-right sm:block">
          <p className="text-sm font-medium text-slate-900">{user?.name}</p>
          <p className="text-xs text-muted-foreground">{user?.title}</p>
        </div>
        <div className="flex h-9 w-9 items-center justify-center rounded-full bg-sky-700 text-xs font-semibold text-white">
          {getInitials(user?.name ?? "MOY")}
        </div>
        <Button size="sm" variant="outline" onClick={() => void logout()}>
          退出
        </Button>
      </div>
    </header>
  );
}
