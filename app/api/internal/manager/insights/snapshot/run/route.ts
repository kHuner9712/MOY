/**
 * v1.5 Internal Manager Insights Snapshot Run API
 * POST /api/internal/manager/insights/snapshot/run
 * 供 pg_cron 定时调用，或管理员手动触发所有 org 的快照生成
 * 不依赖用户 auth，使用服务级别鉴权
 *
 * monthly 语义："上一个完整自然月"
 * 例如：2026-04-01 执行时，生成 2026-03-01 ~ 2026-03-31 的快照
 */

import { NextRequest, NextResponse } from "next/server";
import { createSupabaseAdminClient } from "@/lib/supabase/admin";
import { managerInsightsSnapshotService } from "@/services/manager-insights-snapshot-service";

const INTERNAL_SNAPSHOT_KEY = process.env.INTERNAL_SNAPSHOT_KEY ?? "dev-internal-snapshot-key";

function authenticateInternalRequest(request: NextRequest): boolean {
  const key = request.headers.get("X-Internal-Snapshot-Key");
  return key === INTERNAL_SNAPSHOT_KEY;
}

export async function POST(request: NextRequest) {
  if (!authenticateInternalRequest(request)) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const body = await request.json().catch(() => ({}));
  const snapshotType = (body.snapshotType as "weekly" | "monthly" | undefined) ?? "weekly";
  const targetOrgId = body.orgId as string | undefined;

  try {
    const supabase = createSupabaseAdminClient();
    const results: Array<{
      orgId: string;
      status: "success" | "skipped" | "error";
      snapshotId?: string;
      error?: string;
      periodStart?: string;
      periodEnd?: string;
    }> = [];

    if (targetOrgId) {
      const result = await runSnapshotForOrg(supabase, targetOrgId, snapshotType);
      results.push(result);
    } else {
      const { data: orgs, error: orgsError } = await supabase
        .from("organizations")
        .select("id")
        .limit(100);

      if (orgsError) throw orgsError;
      if (!orgs) return NextResponse.json({ error: "No orgs found" }, { status: 404 });

      for (const org of orgs) {
        const result = await runSnapshotForOrg(supabase, org.id, snapshotType);
        results.push(result);
      }
    }

    const successCount = results.filter((r) => r.status === "success").length;
    const errorCount = results.filter((r) => r.status === "error").length;

    return NextResponse.json({
      snapshotType,
      totalOrgs: results.length,
      successCount,
      errorCount,
      results,
    }, { status: 200 });
  } catch (error) {
    console.error("Internal snapshot run error:", error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Internal server error" },
      { status: 500 }
    );
  }
}

async function runSnapshotForOrg(
  supabase: ReturnType<typeof createSupabaseAdminClient>,
  orgId: string,
  snapshotType: "weekly" | "monthly"
): Promise<{
  orgId: string;
  status: "success" | "skipped" | "error";
  snapshotId?: string;
  error?: string;
  periodStart?: string;
  periodEnd?: string;
}> {
  try {
    const periodDays = snapshotType === "monthly" ? 30 : 7;
    const result = await managerInsightsSnapshotService.generateSnapshot({
      supabase: supabase as never,
      orgId,
      periodDays,
      snapshotType,
    });

    return {
      orgId,
      status: "success",
      snapshotId: result.snapshotId,
      periodStart: result.periodStart,
      periodEnd: result.periodEnd,
    };
  } catch (error) {
    return {
      orgId,
      status: "error",
      error: error instanceof Error ? error.message : "Unknown error",
    };
  }
}
