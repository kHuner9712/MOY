"use client";

import { useMemo, useState } from "react";
import Link from "next/link";

import { useAuth } from "@/components/auth/auth-provider";
import { OpportunityTable } from "@/components/opportunities/opportunity-table";
import { useAppData } from "@/components/shared/app-data-provider";
import { PageHeader } from "@/components/shared/page-header";
import { StatCard } from "@/components/shared/stat-card";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { opportunityStageLabel } from "@/lib/constants";
import type { OpportunityStage } from "@/types/opportunity";
import { BriefcaseBusiness, CircleDollarSign, Layers, TriangleAlert } from "lucide-react";

export default function OpportunitiesPage(): JSX.Element {
  const { user } = useAuth();
  const { opportunities, loading, error } = useAppData();
  const [stage, setStage] = useState<OpportunityStage | "all">("all");

  const scoped = useMemo(() => {
    if (!user) return [];
    const byRole = user.role === "manager" ? opportunities : opportunities.filter((item) => item.ownerId === user.id);
    if (stage === "all") return byRole;
    return byRole.filter((item) => item.stage === stage);
  }, [user, opportunities, stage]);

  const totalAmount = scoped.reduce((sum, item) => sum + item.expectedAmount, 0);
  const highRisk = scoped.filter((item) => item.riskLevel === "high").length;

  if (loading) {
    return <div className="text-sm text-muted-foreground">Loading opportunities...</div>;
  }

  if (error) {
    return <div className="text-sm text-rose-600">Failed to load opportunities: {error}</div>;
  }

  return (
    <div>
      <PageHeader
        title="Opportunities"
        description="Track stage, amount, owner, and risk. Open customer or deal room context for execution."
        action={
          <div className="flex items-center gap-2">
            <div className="w-56">
              <Select value={stage} onValueChange={(value: OpportunityStage | "all") => setStage(value)}>
                <SelectTrigger>
                  <SelectValue placeholder="Filter stage" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All stages</SelectItem>
                  {Object.entries(opportunityStageLabel).map(([key, label]) => (
                    <SelectItem key={key} value={key}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button asChild variant="outline">
              <Link href="/deals">Open Deal Rooms</Link>
            </Button>
          </div>
        }
      />

      <section className="mb-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard title="Opportunities" value={scoped.length} icon={<BriefcaseBusiness className="h-4 w-4 text-sky-700" />} />
        <StatCard
          title="Pipeline Amount"
          value={`¥${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(totalAmount)}`}
          icon={<CircleDollarSign className="h-4 w-4 text-sky-700" />}
        />
        <StatCard title="High Risk" value={highRisk} hint="Prioritize with deal room command" icon={<TriangleAlert className="h-4 w-4 text-rose-600" />} />
        <StatCard title="Current Filter" value={stage === "all" ? "All" : opportunityStageLabel[stage]} icon={<Layers className="h-4 w-4 text-sky-700" />} />
      </section>

      <OpportunityTable items={scoped} />
    </div>
  );
}

