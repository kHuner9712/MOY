"use client";

import Link from "next/link";
import { Building2, Gauge, Layers3, Rocket, Settings2, Shield, ShieldAlert, Sparkles } from "lucide-react";

import { PageHeader } from "@/components/shared/page-header";
import { useAuth } from "@/components/auth/auth-provider";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const cards = [
  {
    href: "/settings/org",
    title: "Organization",
    description: "Brand, locale, default stages, and followup strategy.",
    icon: Building2,
    key: "org"
  },
  {
    href: "/settings/team",
    title: "Team & Seats",
    description: "Members, roles, seat status, and invite management.",
    icon: Shield,
    key: "team"
  },
  {
    href: "/settings/ai",
    title: "AI Control Center",
    description: "Provider/model status, auto capabilities, fallback and human-review policy.",
    icon: Sparkles,
    key: "ai"
  },
  {
    href: "/settings/usage",
    title: "Usage & Quota",
    description: "Org/user usage counters, plan quota and utilization health.",
    icon: Gauge,
    key: "usage"
  },
  {
    href: "/settings/templates",
    title: "Industry Templates",
    description: "Template center for industry-specific stages, packs, checkpoints and demo profile.",
    icon: Layers3,
    key: "templates"
  },
  {
    href: "/settings/automation",
    title: "Automation Rules",
    description: "Operating rule center for health risk, trial stall, deal block and executive actions.",
    icon: ShieldAlert,
    key: "automation"
  },
  {
    href: "/settings/onboarding",
    title: "Onboarding",
    description: "Checklist, demo/trial bootstrap, and setup recommendations.",
    icon: Rocket,
    key: "onboarding"
  }
] as const;

export default function SettingsHubPage(): JSX.Element {
  const { user } = useAuth();
  const isManager = user?.role === "manager";

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
            Current signed-in role: <Badge className="ml-1">{user?.role ?? "unknown"}</Badge>
          </p>
          <p>
            Organization admin actions are available to <code>owner/admin</code> membership users. Managers can access read-only summaries.
          </p>
          {!isManager ? <p>Some management pages may be hidden because your business role is sales.</p> : null}
        </CardContent>
      </Card>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {cards.map((item) => {
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
      </div>
    </div>
  );
}
