import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { listCustomerAiRuns } from "@/services/ai-analysis-service";

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("请先登录", 401);

  const { searchParams } = new URL(request.url);
  const customerId = searchParams.get("customerId");
  const limit = Number(searchParams.get("limit") ?? "8");

  if (!customerId) return fail("缺少 customerId", 400);

  const { data: customerRaw, error: customerError } = await auth.supabase
    .from("customers")
    .select("id, owner_id, org_id")
    .eq("id", customerId)
    .maybeSingle();
  const customer = customerRaw as { id: string; owner_id: string; org_id: string } | null;

  if (customerError) return fail(customerError.message, 500);
  if (!customer) return fail("客户不存在或无权限访问", 404);
  if (customer.org_id !== auth.profile.org_id) return fail("无权限访问其他组织数据", 403);
  if (!isManager(auth.profile) && customer.owner_id !== auth.profile.id) return fail("仅可查看自己负责客户的分析记录", 403);

  try {
    const runs = await listCustomerAiRuns({
      supabase: auth.supabase,
      customerId,
      limit: Number.isFinite(limit) ? Math.max(1, Math.min(20, limit)) : 8
    });
    return ok(runs);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "获取 AI 记录失败", 500);
  }
}

