"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { CalendarCheck2, FileText, Home, PenSquare, Users } from "lucide-react";

import { cn } from "@/lib/utils";

const tabs = [
  { href: "/today", label: "Today", icon: CalendarCheck2 },
  { href: "/capture", label: "Capture", icon: PenSquare },
  { href: "/briefings", label: "Briefings", icon: FileText },
  { href: "/deals", label: "Deals", icon: Users },
  { href: "/dashboard", label: "Home", icon: Home }
] as const;

export function BottomTabBar(): JSX.Element {
  const pathname = usePathname();

  return (
    <nav className="fixed inset-x-0 bottom-0 z-40 border-t bg-white/95 px-2 py-2 backdrop-blur lg:hidden">
      <ul className="grid grid-cols-5 gap-1">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          const active = pathname === tab.href || pathname.startsWith(`${tab.href}/`);
          return (
            <li key={tab.href}>
              <Link
                href={tab.href}
                className={cn(
                  "flex min-h-14 flex-col items-center justify-center rounded-lg text-[11px] font-medium",
                  active ? "bg-sky-100 text-sky-800" : "text-slate-500 hover:bg-slate-100 hover:text-slate-900"
                )}
              >
                <Icon className="mb-1 h-4 w-4" />
                {tab.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
