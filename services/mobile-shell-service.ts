import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getMobileBriefingsView } from "@/services/mobile-brief-service";
import { listMobileDeviceSessions, listMobileDraftSyncJobs, upsertMobileDeviceSession } from "@/services/mobile-draft-service";
import { getMobileTouchpointView } from "@/services/mobile-touchpoint-service";
import { getTodayPlanView } from "@/services/work-plan-service";
import type { Database } from "@/types/database";

type DbClient = ServerSupabaseClient;
type ProfileRow = Database["public"]["Tables"]["profiles"]["Row"];

export async function getMobileBootstrap(params: {
  supabase: DbClient;
  profile: ProfileRow;
  date?: string;
}): Promise<{
  today: {
    focusTheme: string;
    summary: string;
    mustDoCount: number;
    prioritizedCount: number;
  };
  briefings: {
    compactHeadline: string;
    topPriorities: string[];
    urgentRisks: string[];
    oneLineGuidance: string;
  };
  touchpoints: {
    waitingReply: number;
    upcomingMeetings: number;
    documentUpdates: number;
  };
  sync: {
    pending: number;
    failed: number;
    recentSessions: Array<{ id: string; deviceLabel: string; installType: string; lastSeenAt: string }>;
  };
  manager: {
    escalatedDeals: number;
    blockedCheckpoints: number;
    openInterventions: number;
  } | null;
}> {
  const [planView, briefings, touchpoints, syncJobs, sessions] = await Promise.all([
    getTodayPlanView({
      supabase: params.supabase,
      orgId: params.profile.org_id,
      userId: params.profile.id,
      date: params.date
    }),
    getMobileBriefingsView({
      supabase: params.supabase,
      profile: params.profile,
      date: params.date
    }),
    getMobileTouchpointView({
      supabase: params.supabase,
      profile: params.profile
    }),
    listMobileDraftSyncJobs({
      supabase: params.supabase,
      orgId: params.profile.org_id,
      userId: params.profile.id,
      statuses: ["pending", "failed"],
      limit: 100
    }),
    listMobileDeviceSessions({
      supabase: params.supabase,
      orgId: params.profile.org_id,
      userId: params.profile.id,
      limit: 4
    })
  ]);

  const mustDoCount = (planView?.planItems ?? []).filter((item) => item.mustDo).length;
  const prioritizedCount = planView?.planItems.length ?? 0;

  let manager: {
    escalatedDeals: number;
    blockedCheckpoints: number;
    openInterventions: number;
  } | null = null;

  if (params.profile.role === "manager") {
    const [dealsRes, checkpointsRes, interventionsRes] = await Promise.all([
      params.supabase
        .from("deal_rooms")
        .select("id", { count: "exact", head: true })
        .eq("org_id", params.profile.org_id)
        .in("room_status", ["escalated", "blocked"]),
      params.supabase
        .from("deal_checkpoints")
        .select("id", { count: "exact", head: true })
        .eq("org_id", params.profile.org_id)
        .eq("status", "blocked"),
      params.supabase
        .from("intervention_requests")
        .select("id", { count: "exact", head: true })
        .eq("org_id", params.profile.org_id)
        .in("status", ["open", "accepted"])
    ]);
    if (dealsRes.error) throw new Error(dealsRes.error.message);
    if (checkpointsRes.error) throw new Error(checkpointsRes.error.message);
    if (interventionsRes.error) throw new Error(interventionsRes.error.message);
    manager = {
      escalatedDeals: dealsRes.count ?? 0,
      blockedCheckpoints: checkpointsRes.count ?? 0,
      openInterventions: interventionsRes.count ?? 0
    };
  }

  return {
    today: {
      focusTheme: planView?.plan.focusTheme ?? "先处理关键任务，再推进高价值客户。",
      summary: planView?.plan.summary ?? "尚未生成今日计划，可先执行 must-do 任务。",
      mustDoCount,
      prioritizedCount
    },
    briefings: {
      compactHeadline: briefings.compactHeadline,
      topPriorities: briefings.topPriorities,
      urgentRisks: briefings.urgentRisks,
      oneLineGuidance: briefings.oneLineGuidance
    },
    touchpoints: {
      waitingReply: touchpoints.summary.waitingReplyThreads,
      upcomingMeetings: touchpoints.summary.upcomingMeetings,
      documentUpdates: touchpoints.summary.documentUpdates
    },
    sync: {
      pending: syncJobs.filter((item) => item.syncStatus === "pending").length,
      failed: syncJobs.filter((item) => item.syncStatus === "failed").length,
      recentSessions: sessions.map((item) => ({
        id: item.id,
        deviceLabel: item.deviceLabel,
        installType: item.installType,
        lastSeenAt: item.lastSeenAt
      }))
    },
    manager
  };
}

export async function recordMobileInstallSession(params: {
  supabase: DbClient;
  profile: ProfileRow;
  deviceLabel: string;
  installType: "browser" | "pwa";
  appVersion?: string | null;
  pushCapable?: boolean;
  metadata?: Record<string, unknown>;
}) {
  return upsertMobileDeviceSession({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    userId: params.profile.id,
    deviceLabel: params.deviceLabel,
    installType: params.installType,
    appVersion: params.appVersion,
    pushCapable: params.pushCapable,
    metadata: params.metadata
  });
}

export async function getMobileTodayLite(params: {
  supabase: DbClient;
  profile: ProfileRow;
  date?: string;
}) {
  const planView = await getTodayPlanView({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    userId: params.profile.id,
    date: params.date
  });
  let alertsQuery = params.supabase
    .from("alerts")
    .select("id, title, severity, status, owner_id, customer_id, created_at")
    .eq("org_id", params.profile.org_id)
    .in("status", ["open", "watching"])
    .order("created_at", { ascending: false })
    .limit(20);
  if (params.profile.role !== "manager") {
    alertsQuery = alertsQuery.eq("owner_id", params.profile.id);
  }
  const alertsRes = await alertsQuery;
  if (alertsRes.error) throw new Error(alertsRes.error.message);

  return {
    plan: planView,
    alerts: alertsRes.data ?? []
  };
}
