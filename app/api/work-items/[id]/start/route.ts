import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { getWorkItemById, updateWorkItemStatus } from "@/services/work-item-service";

interface RouteParams {
  params: { id: string };
}

export async function POST(_request: Request, { params }: RouteParams) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  try {
    const item = await getWorkItemById({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      workItemId: params.id
    });
    if (!item) return fail("Work item not found", 404);
    if (!isManager(auth.profile) && item.ownerId !== auth.profile.id) {
      return fail("Sales can only operate own work items", 403);
    }

    const updated = await updateWorkItemStatus({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      workItemId: params.id,
      actorUserId: auth.profile.id,
      operation: "start",
      note: "Start from workbench"
    });
    return ok(updated);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "work_item_start_failed", 500);
  }
}
