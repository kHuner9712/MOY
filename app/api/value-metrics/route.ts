import { createSupabaseServerClient } from "@/lib/supabase/server";
import { getWeeklyValueMetrics, generateValueMetricsSummary } from "@/services/value-metrics-service";
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

    if (!typedProfile?.org_id) {
      return NextResponse.json({ success: false, error: "No organization found" }, { status: 400 });
    }

    const orgId = typedProfile.org_id;
    const profileId = typedProfile.id;

    const url = new URL(request.url);
    const ownerId = url.searchParams.get("ownerId") ?? undefined;
    const generateSummary = url.searchParams.get("summary") === "true";

    if (generateSummary) {
      const result = await generateValueMetricsSummary({
        supabase,
        orgId,
        actorUserId: profileId,
        ownerId
      });

      return NextResponse.json({
        success: true,
        data: result.summary,
        meta: {
          usedFallback: result.usedFallback,
          fallbackReason: result.fallbackReason
        }
      });
    }

    const result = await getWeeklyValueMetrics({
      supabase,
      orgId,
      ownerId
    });

    return NextResponse.json({
      success: true,
      data: result
    });
  } catch (error) {
    console.error("[api/value-metrics] Error:", error);
    return NextResponse.json(
      {
        success: false,
        error: error instanceof Error ? error.message : "Failed to get value metrics"
      },
      { status: 500 }
    );
  }
}
