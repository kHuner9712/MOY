"use client";

import Link from "next/link";
import { Building2, Gauge, GitCompareArrows, Layers3, Rocket, Settings2, Shield, ShieldAlert, Sparkles, Telescope, Waypoints } from "lucide-react";

import { PageHeader } from "@/components/shared/page-header";
import { useAuth } from "@/components/auth/auth-provider";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { canManageOrgCustomization, canManageTemplates, canViewManagerWorkspace, canViewOrgUsage, resolveEffectiveOrgRole } from "@/lib/role-capability";

const cards = [
  {
    href: "/settings/org",
    title: "Organization",
    description: "Brand, locale, default stages, and followup strategy.",
    icon: Building2,
    key: "org",
    requiredAccess: "manager"
  },
  {
    href: "/settings/team",
    title: "Team & Seats",
    description: "Members, roles, seat status, and invite management.",
    icon: Shield,
    key: "team",
    requiredAccess: "manager"
  },
  {
    href: "/settings/ai",
    title: "AI Control Center",
    description: "Provider/model status, auto capabilities, fallback and human-review policy.",
    icon: Sparkles,
    key: "ai",
    requiredAccess: "manager"
  },
  {
    href: "/settings/usage",
    title: "Usage & Quota",
    description: "Org/user usage counters, plan quota and utilization health.",
    icon: Gauge,
    key: "usage",
    requiredAccess: "usage"
  },
  {
    href: "/settings/templates",
    title: "Industry Templates",
    description: "Template center for industry-specific stages, packs, checkpoints and demo profile.",
    icon: Layers3,
    key: "templates",
    requiredAccess: "manager"
  },
  {
    href: "/settings/automation",
    title: "Automation Rules",
    description: "Operating rule center for health risk, trial stall, deal block and executive actions.",
    icon: ShieldAlert,
    key: "automation",
    requiredAccess: "manager"
  },
  {
    href: "/settings/onboarding",
    title: "Onboarding",
    description: "Checklist, demo/trial bootstrap, and setup recommendations.",
    icon: Rocket,
    key: "onboarding",
    requiredAccess: "manager"
  },
  {
    href: "/settings/config-ops",
    title: "Config Operations Hub v1",
    description: "Unified read hub for runtime explain, recent config changes, diagnostics/conflicts and rollback readiness.",
    icon: Waypoints,
    key: "config-ops",
    requiredAccess: "manager"
  },
  {
    href: "/settings/config-timeline",
    title: "Config Timeline & Diff v1",
    description: "Read-only cross-domain timeline with before/after summaries and structured diff.",
    icon: GitCompareArrows,
    key: "config-timeline",
    requiredAccess: "manager"
  },
  {
    href: "/settings/org-config",
    title: "Org Config Editor v1",
    description: "Governed editor for org settings, AI settings and feature flags with diagnostics and version baseline.",
    icon: Settings2,
    key: "org-config",
    requiredAccess: "manager"
  },
  {
    href: "/settings/runtime-debug",
    title: "Runtime Explain Debug",
    description: "Read-only runtime config source explain, ignored overrides and governance status.",
    icon: Telescope,
    key: "runtime-debug",
    requiredAccess: "manager"
  }
] as const;

export default function SettingsHubPage(): JSX.Element {
  const { user } = useAuth();
  const canAccessManagerSettings = canViewManagerWorkspace(user);
  const canAccessUsage = canViewOrgUsage(user);
  const canWriteTemplates = canManageTemplates(user);
  const canWriteCustomization = canManageOrgCustomization(user);
  const effectiveOrgRole = resolveEffectiveOrgRole(user) ?? "unknown";
  const visibleCards = cards.filter((item) => {
    if (item.requiredAccess === "usage") return canAccessUsage;
    return canAccessManagerSettings;
  });

  return (
    <div>
      <PageHeader
        title="Settings"
        description="Organization productization center for permissions, AI control, usage quota and onboarding readiness."
      />

      <Card className="mb-5 border-sky-100 bg-sky-50/60">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base font-semibold text-sky-900">
            <Settings2 className="h-4 w-4" />
            Productization Layer
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-sky-900/80">
          <p>
            Current display role: <Badge className="ml-1">{user?.role ?? "unknown"}</Badge>
          </p>
          <p>
            Current organization role: <Badge className="ml-1">{effectiveOrgRole}</Badge>
          </p>
          <p>
            Organization admin actions are available to <code>owner/admin</code> membership users. Managers can access scoped settings and read summaries.
          </p>
          <p>
            Template write: <Badge variant={canWriteTemplates ? "default" : "secondary"} className="ml-1">{canWriteTemplates ? "allowed" : "read-only"}</Badge>
            {" "}
            Customization write: <Badge variant={canWriteCustomization ? "default" : "secondary"} className="ml-1">{canWriteCustomization ? "allowed" : "read-only"}</Badge>
          </p>
          {!canAccessManagerSettings ? <p>Management settings are hidden because your current role does not have manager-level workspace access.</p> : null}
        </CardContent>
      </Card>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {visibleCards.map((item) => {
          const Icon = item.icon;
          return (
            <Link key={item.key} href={item.href} className="group block">
              <Card className="h-full transition hover:border-sky-300 hover:shadow-sm">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base text-slate-900">
                    <Icon className="h-4 w-4 text-sky-700" />
                    {item.title}
                  </CardTitle>
                </CardHeader>
                <CardContent className="text-sm text-muted-foreground">{item.description}</CardContent>
              </Card>
            </Link>
          );
        })}
        {visibleCards.length === 0 ? (
          <Card className="md:col-span-2 xl:col-span-3">
            <CardContent className="p-6 text-sm text-muted-foreground">
              No management settings are available for your current role. Contact your organization owner/admin if you need broader access.
            </CardContent>
          </Card>
        ) : null}
      </div>
    </div>
  );
}
