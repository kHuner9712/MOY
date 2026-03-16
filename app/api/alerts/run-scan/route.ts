import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { runOrganizationAlertScan } from "@/services/alert-rule-engine";

export async function POST() {
  const auth = await getServerAuthContext();
  if (!auth) return fail("请先登录", 401);
  if (!isManager(auth.profile)) return fail("仅管理者可执行漏单扫描", 403);

  try {
    const result = await runOrganizationAlertScan({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      triggeredByUserId: auth.profile.id,
      withAiInference: true
    });

    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "漏单扫描失败", 500);
  }
}
