import type { UserRole } from "@/types/auth";

export interface NavItem {
  href: string;
  label: string;
  key:
    | "dashboard"
    | "today"
    | "capture"
    | "customers"
    | "touchpoints"
    | "followups"
    | "opportunities"
    | "deals"
    | "imports"
    | "alerts"
    | "reports"
    | "briefings"
    | "playbooks"
    | "memory"
    | "manager"
    | "quality"
    | "rhythm"
    | "outcomes"
    | "conversion"
    | "growth"
    | "executive"
    | "automation"
    | "settings";
  roles: UserRole[];
}

export const navItems: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", key: "dashboard", roles: ["sales", "manager"] },
  { href: "/today", label: "Today Tasks", key: "today", roles: ["sales", "manager"] },
  { href: "/capture", label: "Quick Capture", key: "capture", roles: ["sales", "manager"] },
  { href: "/customers", label: "Customers", key: "customers", roles: ["sales", "manager"] },
  { href: "/touchpoints", label: "Touchpoints", key: "touchpoints", roles: ["sales", "manager"] },
  { href: "/reports", label: "Reports", key: "reports", roles: ["sales", "manager"] },
  { href: "/briefings", label: "Briefings", key: "briefings", roles: ["sales", "manager"] },
  { href: "/playbooks", label: "Playbooks", key: "playbooks", roles: ["sales", "manager"] },
  { href: "/memory", label: "Work Memory", key: "memory", roles: ["sales", "manager"] },
  { href: "/followups/new", label: "New Followup", key: "followups", roles: ["sales", "manager"] },
  { href: "/opportunities", label: "Opportunities", key: "opportunities", roles: ["sales", "manager"] },
  { href: "/deals", label: "Deal Rooms", key: "deals", roles: ["sales", "manager"] },
  { href: "/growth", label: "Growth Pipeline", key: "growth", roles: ["sales", "manager"] },
  { href: "/imports", label: "Import Center", key: "imports", roles: ["manager"] },
  { href: "/alerts", label: "Alerts", key: "alerts", roles: ["sales", "manager"] },
  { href: "/manager", label: "Manager Board", key: "manager", roles: ["manager"] },
  { href: "/manager/quality", label: "Operating Quality", key: "quality", roles: ["manager"] },
  { href: "/manager/rhythm", label: "Execution Rhythm", key: "rhythm", roles: ["manager"] },
  { href: "/manager/outcomes", label: "Outcome Intelligence", key: "outcomes", roles: ["manager"] },
  { href: "/manager/conversion", label: "Conversion View", key: "conversion", roles: ["manager"] },
  { href: "/executive", label: "Executive Cockpit", key: "executive", roles: ["manager"] },
  { href: "/settings/automation", label: "Automation Rules", key: "automation", roles: ["manager"] },
  { href: "/settings", label: "Settings", key: "settings", roles: ["sales", "manager"] }
];

export function canAccessPath(role: UserRole, path: string): boolean {
  const candidates = navItems.filter((item) => path.startsWith(item.href));
  if (candidates.length === 0) return false;
  const best = candidates.sort((a, b) => b.href.length - a.href.length)[0];
  return best.roles.includes(role);
}

export function getRoleHome(_role: UserRole): string {
  return "/dashboard";
}
