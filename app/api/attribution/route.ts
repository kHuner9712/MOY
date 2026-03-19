import { createSupabaseServerClient } from "@/lib/supabase/server";
import {
  traceEventToOutcome,
  getOrgAttributionStats,
  getAttributionSummaryForBriefing
} from "@/services/attribution-service";
import { NextResponse } from "next/server";

export async function GET(request: Request) {
  try {
    const supabase = createSupabaseServerClient();
    if (!supabase) {
      return NextResponse.json({ success: false, error: "Supabase not configured" }, { status: 500 });
    }

    const {
      data: { user }
    } = await supabase.auth.getUser();

    if (!user) {
      return NextResponse.json({ success: false, error: "Unauthorized" }, { status: 401 });
    }

    const { data: profile } = await supabase
      .from("profiles")
      .select("id, org_id, role")
      .eq("id", user.id)
      .maybeSingle();

    type ProfileRow = { id: string; org_id: string | null; role: string };
    const typedProfile = profile as ProfileRow | null;

    if (!typedProfile || !typedProfile.org_id) {
      return NextResponse.json({ success: false, error: "No organization found" }, { status: 400 });
    }

    const orgId = typedProfile.org_id;

    const url = new URL(request.url);
    const action = url.searchParams.get("action") ?? "stats";
    const eventId = url.searchParams.get("eventId") ?? undefined;
    const periodStart = url.searchParams.get("periodStart") ?? getWeekStart();
    const periodEnd = url.searchParams.get("periodEnd") ?? getWeekEnd();
    const ownerId = url.searchParams.get("ownerId") ?? undefined;

    if (action === "trace" && eventId) {
      const chain = await traceEventToOutcome({
        supabase,
        orgId,
        eventId
      });

      return NextResponse.json({
        success: true,
        data: chain
      });
    }

    if (action === "briefing") {
      const summary = await getAttributionSummaryForBriefing({
        supabase,
        orgId,
        periodStart,
        periodEnd
      });

      return NextResponse.json({
        success: true,
        data: summary
      });
    }

    const stats = await getOrgAttributionStats({
      supabase,
      orgId,
      periodStart,
      periodEnd,
      ownerId
    });

    return NextResponse.json({
      success: true,
      data: stats
    });
  } catch (error) {
    console.error("[api/attribution] Error:", error);
    return NextResponse.json(
      {
        success: false,
        error: error instanceof Error ? error.message : "Failed to get attribution data"
      },
      { status: 500 }
    );
  }
}

function getWeekStart(): string {
  const now = new Date();
  const day = now.getDay();
  const diff = now.getDate() - day + (day === 0 ? -6 : 1);
  const weekStart = new Date(now.setDate(diff));
  return weekStart.toISOString().split("T")[0];
}

function getWeekEnd(): string {
  const now = new Date();
  const day = now.getDay();
  const diff = now.getDate() - day + (day === 0 ? -6 : 1) + 6;
  const weekEnd = new Date(now.setDate(diff));
  return weekEnd.toISOString().split("T")[0];
}
