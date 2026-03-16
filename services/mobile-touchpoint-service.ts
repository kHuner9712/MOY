import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getTouchpointHubView, touchpointEventSummary } from "@/services/external-touchpoint-service";
import type { Database } from "@/types/database";

type DbClient = ServerSupabaseClient;
type ProfileRow = Database["public"]["Tables"]["profiles"]["Row"];

export async function getMobileTouchpointView(params: {
  supabase: DbClient;
  profile: ProfileRow;
  customerId?: string;
  dealRoomId?: string;
}): Promise<{
  waitingReply: Array<{ id: string; subject: string; customerName: string | null; latestMessageAt: string | null }>;
  upcomingMeetings: Array<{ id: string; title: string; startAt: string; customerName: string | null }>;
  recentDocuments: Array<{ id: string; title: string; documentType: string; customerName: string | null; updatedAt: string }>;
  summary: {
    totalEvents: number;
    waitingReplyThreads: number;
    upcomingMeetings: number;
    documentUpdates: number;
  };
}> {
  const ownerId = params.profile.role === "manager" ? null : params.profile.id;
  const [hub, summary] = await Promise.all([
    getTouchpointHubView({
      supabase: params.supabase,
      orgId: params.profile.org_id,
      ownerId,
      customerId: params.customerId ?? null,
      dealRoomId: params.dealRoomId ?? null,
      limit: 30
    }),
    touchpointEventSummary({
      supabase: params.supabase,
      orgId: params.profile.org_id,
      ownerId,
      sinceDays: 7
    })
  ]);

  return {
    waitingReply: hub.emailThreads
      .filter((item) => item.threadStatus === "waiting_reply")
      .slice(0, 10)
      .map((item) => ({
        id: item.id,
        subject: item.subject,
        customerName: item.customerName,
        latestMessageAt: item.latestMessageAt
      })),
    upcomingMeetings: hub.calendarEvents
      .filter((item) => item.meetingStatus === "scheduled")
      .slice(0, 10)
      .map((item) => ({
        id: item.id,
        title: item.title,
        startAt: item.startAt,
        customerName: item.customerName
      })),
    recentDocuments: hub.documentAssets.slice(0, 10).map((item) => ({
      id: item.id,
      title: item.title,
      documentType: item.documentType,
      customerName: item.customerName,
      updatedAt: item.updatedAt
    })),
    summary
  };
}
