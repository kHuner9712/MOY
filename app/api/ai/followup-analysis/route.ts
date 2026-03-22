import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { runFollowupAnalysis } from "@/services/ai-analysis-service";
import { checkOrgAiScenarioAccess } from "@/services/feature-access-service";

const requestSchema = z.object({
  customerId: z.string().uuid(),
  followupId: z.string().uuid()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const payload = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(payload);
  if (!parsed.success) return fail("Invalid request payload", 400);

  const { customerId, followupId } = parsed.data;

  const customerRes = await auth.supabase
    .from("customers")
    .select("id, owner_id, org_id")
    .eq("id", customerId)
    .maybeSingle();

  if (customerRes.error) return fail(customerRes.error.message, 500);
  const customer = (customerRes.data ?? null) as { id: string; owner_id: string; org_id: string } | null;
  if (!customer) return fail("Customer not found", 404);
  if (customer.org_id !== auth.profile.org_id) return fail("Cross-org access is forbidden", 403);
  if (!isManager(auth.profile) && customer.owner_id !== auth.profile.id) return fail("Sales can only analyze owned customers", 403);

  try {
    const access = await checkOrgAiScenarioAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      featureKey: "ai_auto_analysis",
      settingKey: "autoAnalysisEnabled",
      refreshUsage: true
    });
    if (!access.allowed) {
      const status = access.reason?.toLowerCase().includes("quota") ? 429 : 403;
      return fail(access.reason ?? "AI auto analysis disabled", status);
    }

    const result = await runFollowupAnalysis({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      customerId,
      followupId,
      triggeredByUserId: auth.profile.id,
      triggerSource: "followup_submit"
    });

    return ok({
      runId: result.run.id,
      status: result.run.status,
      resultSource: result.run.result_source,
      usedFallback: result.usedFallback,
      fallbackReason: result.run.fallback_reason,
      result: result.result,
      leakInference: result.leakInference,
      leakAlertAction: result.leakAlertAction,
      alertWorkItem: result.alertWorkItem
    });
  } catch (error) {
    return fail(error instanceof Error ? error.message : "followup_analysis_failed", 500);
  }
}
