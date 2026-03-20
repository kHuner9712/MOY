/**
 * v1.3 Manager Insights API
 * GET /api/manager/insights
 */

import { NextRequest, NextResponse } from "next/server";
import { getServerAuthContext } from "@/lib/server-auth";
import { managerInsightsService } from "@/services/manager-insights-service";
import { fail } from "@/lib/api-response";

export async function GET(request: NextRequest) {
  try {
    const auth = await getServerAuthContext();
    if (!auth) return fail("Unauthorized", 401);

    const { searchParams } = new URL(request.url);
    const periodDays = parseInt(searchParams.get("periodDays") ?? "7", 10);

    const result = await managerInsightsService.getInsights({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      ownerId: auth.profile.id,
      periodDays: isNaN(periodDays) ? 7 : periodDays
    });

    return NextResponse.json(result);
  } catch (error) {
    console.error("Manager insights error:", error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Internal server error" },
      { status: 500 }
    );
  }
}
