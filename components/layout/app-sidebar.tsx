"use client";

import Link from "next/link";
import {
  BarChart3,
  BellRing,
  BrainCircuit,
  BookMarked,
  BriefcaseBusiness,
  CalendarCheck2,
  ClipboardPenLine,
  Mail,
  FileText,
  Handshake,
  Sheet,
  LayoutDashboard,
  PenSquare,
  Settings,
  ShieldAlert,
  TimerReset,
  TrendingUp,
  Users
} from "lucide-react";

import { navItems } from "@/lib/auth";
import { cn } from "@/lib/utils";
import type { UserRole } from "@/types/auth";

const iconMap = {
  dashboard: LayoutDashboard,
  today: CalendarCheck2,
  capture: PenSquare,
  customers: Users,
  touchpoints: Mail,
  followups: ClipboardPenLine,
  opportunities: BriefcaseBusiness,
  deals: Handshake,
  imports: Sheet,
  alerts: BellRing,
  reports: FileText,
  briefings: FileText,
  playbooks: BookMarked,
  memory: BrainCircuit,
  manager: BarChart3,
  quality: BarChart3,
  rhythm: TimerReset,
  outcomes: TrendingUp,
  conversion: TrendingUp,
  growth: TrendingUp,
  executive: ShieldAlert,
  automation: ShieldAlert,
  settings: Settings
} as const;

interface AppSidebarProps {
  role: UserRole;
  pathname: string;
  onNavigate?: () => void;
}

export function AppSidebar({ role, pathname, onNavigate }: AppSidebarProps): JSX.Element {
  const items = navItems.filter((item) => item.roles.includes(role));

  return (
    <aside className="flex h-full w-64 flex-col border-r bg-white/85 backdrop-blur-sm">
      <div className="border-b px-6 py-6">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-sky-700">Tongming Technology</p>
        <h1 className="mt-2 font-display text-2xl font-bold tracking-wide text-slate-900">MOY MoYan</h1>
        <p className="mt-2 text-xs text-muted-foreground">Mate Of You | Sales AI Workspace</p>
      </div>

      <nav className="flex-1 space-y-1 px-3 py-4">
        {items.map((item) => {
          const Icon = iconMap[item.key];
          const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={onNavigate}
              className={cn(
                "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                active ? "bg-sky-100 text-sky-800" : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
              )}
            >
              <Icon className="h-4 w-4" />
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="border-t px-4 py-3 text-xs text-muted-foreground">
        <p>MOY MVP | Phase 16 Automation Ops</p>
      </div>
    </aside>
  );
}
