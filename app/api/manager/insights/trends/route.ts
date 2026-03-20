/**
 * v1.4 Manager Insights Trends API
 * GET /api/manager/insights/trends
 * 读取历史趋势数据
 */

import { NextRequest, NextResponse } from "next/server";
import { getServerAuthContext } from "@/lib/server-auth";
import { managerInsightsSnapshotService } from "@/services/manager-insights-snapshot-service";
import { fail } from "@/lib/api-response";

export async function GET(request: NextRequest) {
  try {
    const auth = await getServerAuthContext();
    if (!auth) return fail("Unauthorized", 401);

    const { searchParams } = new URL(request.url);
    const snapshotType = (searchParams.get("snapshotType") as "weekly" | "monthly" | null) ?? "weekly";
    const periods = parseInt(searchParams.get("periods") ?? "8", 10);

    const result = await managerInsightsSnapshotService.getTrends({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      snapshotType,
      periods: isNaN(periods) ? 8 : periods,
    });

    return NextResponse.json(result);
  } catch (error) {
    console.error("Trends error:", error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Internal server error" },
      { status: 500 }
    );
  }
}
