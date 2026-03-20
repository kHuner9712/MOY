/**
 * v1.5 Manager Insights Snapshot Backfill API
 * POST /api/manager/insights/snapshots/backfill
 * 允许 manager 主动回填历史快照（仅限自己的 org）
 */

import { NextRequest, NextResponse } from "next/server";
import { getServerAuthContext } from "@/lib/server-auth";
import { managerInsightsSnapshotService } from "@/services/manager-insights-snapshot-service";
import { fail } from "@/lib/api-response";

export async function POST(request: NextRequest) {
  try {
    const auth = await getServerAuthContext();
    if (!auth) return fail("Unauthorized", 401);
    if (!auth.profile.role?.includes("manager")) {
      return fail("Manager role required", 403);
    }

    const body = await request.json().catch(() => ({}));
    const snapshotType = (body.snapshotType as "weekly" | "monthly") ?? "weekly";
    const periodsToBackfill = Math.min(Number(body.periodsToBackfill) || 8, 52);

    const result = await managerInsightsSnapshotService.backfillHistoricalSnapshots({
      supabase: auth.supabase as never,
      orgId: auth.profile.org_id,
      snapshotType,
      periodsToBackfill,
    });

    return NextResponse.json(result, { status: 200 });
  } catch (error) {
    console.error("Snapshot backfill error:", error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Internal server error" },
      { status: 500 }
    );
  }
}
