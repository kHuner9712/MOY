import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { createWorkItemFromAlert } from "@/services/work-item-service";

const requestSchema = z.object({
  alertId: z.string().uuid()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(body);
  if (!parsed.success) return fail("Invalid request payload", 400);

  const { alertId } = parsed.data;

  try {
    const { data: alertRaw, error: alertError } = await auth.supabase
      .from("alerts")
      .select("id, owner_id, org_id")
      .eq("id", alertId)
      .eq("org_id", auth.profile.org_id)
      .single();
    const alert = alertRaw as { id: string; owner_id: string | null; org_id: string } | null;
    if (alertError || !alert) return fail(alertError?.message ?? "Alert not found", 404);

    if (!isManager(auth.profile) && alert.owner_id !== auth.profile.id) {
      return fail("Sales can only convert own alerts", 403);
    }

    const result = await createWorkItemFromAlert({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      alertId,
      actorUserId: auth.profile.id
    });

    return ok({
      workItem: result.workItem,
      created: result.created
    });
  } catch (error) {
    return fail(error instanceof Error ? error.message : "convert_alert_failed", 500);
  }
}
