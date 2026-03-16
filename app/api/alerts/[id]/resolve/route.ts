import { fail, ok } from "@/lib/api-response";
import { canOperateAlert, isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { resolveAlert } from "@/services/alert-workflow-service";
import { completeWorkItemsBySourceRef } from "@/services/work-item-service";

export async function POST(_request: Request, context: { params: { id: string } }) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("请先登录", 401);

  const alertId = context.params.id;
  if (!alertId) return fail("缺少提醒 ID", 400);

  const { data: alertRaw, error } = await auth.supabase.from("alerts").select("id, org_id, owner_id, status").eq("id", alertId).maybeSingle();
  const alert = alertRaw as { id: string; org_id: string; owner_id: string | null; status: string } | null;

  if (error) return fail(error.message, 500);
  if (!alert) return fail("提醒不存在或无权限访问", 404);
  if (!isManager(auth.profile) && !canOperateAlert(auth.profile, alert)) return fail("无权限处理该提醒", 403);

  try {
    await resolveAlert({
      supabase: auth.supabase,
      alertId
    });

    await completeWorkItemsBySourceRef({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      actorUserId: auth.profile.id,
      sourceRefType: "alert",
      sourceRefId: alertId,
      note: "Auto completed after alert resolved"
    });

    return ok({
      id: alertId,
      status: "resolved"
    });
  } catch (cause) {
    return fail(cause instanceof Error ? cause.message : "标记解决失败", 500);
  }
}
