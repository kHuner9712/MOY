"use client";

import { useAuth } from "@/components/auth/auth-provider";
import { useExecutiveActions } from "@/hooks/use-executive-actions";
import { useExecutiveCockpit } from "@/hooks/use-executive-cockpit";

import { ExecutiveBriefsPanel } from "./_components/executive-briefs-panel";
import { ExecutiveEventsPanel } from "./_components/executive-events-panel";
import { ExecutiveHeader } from "./_components/executive-header";
import { ExecutiveHealthPanel } from "./_components/executive-health-panel";
import { ExecutiveRuleRunsPanel } from "./_components/executive-rule-runs-panel";
import { ExecutiveStats } from "./_components/executive-stats";

export default function ExecutiveCockpitPage(): JSX.Element {
  const { user } = useAuth();
  const isManager = user?.role === "manager";
  const { summary, events, briefs, loading, error, reload } = useExecutiveCockpit(isManager);
  const {
    health,
    healthLoading,
    actionMessage,
    busyEventId,
    generatingBrief,
    briefType,
    riskSnapshots,
    setBriefType,
    runEventAction,
    runRefresh,
    generateBrief
  } = useExecutiveActions({
    enabled: isManager,
    reload
  });

  if (!isManager) {
    return <div className="text-sm text-muted-foreground">Only manager can access executive cockpit.</div>;
  }

  if (loading || !summary) return <div className="text-sm text-muted-foreground">Loading executive cockpit...</div>;
  if (error) return <div className="text-sm text-rose-600">Failed to load executive cockpit: {error}</div>;

  return (
    <div>
      <ExecutiveHeader
        briefType={briefType}
        generatingBrief={generatingBrief}
        onBriefTypeChange={setBriefType}
        onRefresh={() => void runRefresh()}
        onGenerateBrief={() => void generateBrief()}
      />

      {actionMessage ? <p className="mb-3 text-sm text-muted-foreground">{actionMessage}</p> : null}

      <ExecutiveStats summary={summary} />

      <ExecutiveBriefsPanel recommendations={summary.recommendations} briefs={briefs} />

      <section className="mb-4 grid gap-4 xl:grid-cols-2">
        <ExecutiveEventsPanel events={events} busyEventId={busyEventId} onEventAction={(eventId, action) => void runEventAction(eventId, action)} />
        <ExecutiveHealthPanel health={health} healthLoading={healthLoading} riskSnapshots={riskSnapshots} />
      </section>

      <ExecutiveRuleRunsPanel runs={summary.recentRuleRuns} />
    </div>
  );
}
