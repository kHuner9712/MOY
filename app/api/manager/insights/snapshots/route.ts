/**
 * v1.4 Manager Insights Snapshot API
 * POST /api/manager/insights/snapshots
 * 生成 manager insights 快照
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
    const periodDays = (body.periodDays as number | undefined) ?? 7;
    const snapshotType = (body.snapshotType as string | undefined) ?? "weekly";

    const result = await managerInsightsSnapshotService.generateSnapshot({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      periodDays,
      snapshotType: snapshotType as "weekly" | "monthly",
    });

    return NextResponse.json(result, { status: 201 });
  } catch (error) {
    console.error("Snapshot generation error:", error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Internal server error" },
      { status: 500 }
    );
  }
}
