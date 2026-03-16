import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { runCustomerHealthAnalysis, runLeakRiskInference } from "@/services/ai-analysis-service";
import { checkOrgAiScenarioAccess } from "@/services/feature-access-service";

const requestSchema = z.object({
  customerId: z.string().uuid()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const payload = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(payload);
  if (!parsed.success) return fail("Invalid request payload", 400);

  const { customerId } = parsed.data;
  const { data: customerRaw, error: customerError } = await auth.supabase
    .from("customers")
    .select("id, owner_id, org_id")
    .eq("id", customerId)
    .maybeSingle();
  const customer = customerRaw as { id: string; owner_id: string; org_id: string } | null;

  if (customerError) return fail(customerError.message, 500);
  if (!customer) return fail("Customer not found", 404);
  if (customer.org_id !== auth.profile.org_id) return fail("Cross-org access is forbidden", 403);
  if (!isManager(auth.profile) && customer.owner_id !== auth.profile.id) {
    return fail("Sales can only analyze owned customers", 403);
  }

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

    const health = await runCustomerHealthAnalysis({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      customerId,
      triggeredByUserId: auth.profile.id,
      triggerSource: "manual"
    });

    let leak: Awaited<ReturnType<typeof runLeakRiskInference>> | null = null;
    try {
      leak = await runLeakRiskInference({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        customerId,
        triggeredByUserId: auth.profile.id,
        triggerSource: "manual"
      });
    } catch (leakError) {
      console.error("[api.ai.customer-analysis] leak_inference_failed", {
        customer_id: customerId,
        error: leakError instanceof Error ? leakError.message : "unknown"
      });
    }

    return ok({
      customerHealthRunId: health.run.id,
      leakRunId: leak?.run.id ?? null,
      customerHealth: health.result,
      leakInference: leak?.result ?? null,
      leakAlertAction: leak?.alertAction ?? null,
      usedFallback: health.usedFallback || leak?.usedFallback === true
    });
  } catch (error) {
    return fail(error instanceof Error ? error.message : "customer_analysis_failed", 500);
  }
}
