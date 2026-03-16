import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { convertWorkItemToFollowup, getWorkItemById, updateWorkItemStatus } from "@/services/work-item-service";

interface RouteParams {
  params: { id: string };
}

const requestSchema = z.object({
  note: z.string().max(500).optional(),
  convertToFollowup: z.boolean().optional()
});

export async function POST(request: Request, { params }: RouteParams) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

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

    if (parsed.data.convertToFollowup) {
      const converted = await convertWorkItemToFollowup({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        workItemId: params.id,
        actorUserId: auth.profile.id
      });
      return ok({
        ...converted.workItem,
        followupId: converted.followupId
      });
    }

    const updated = await updateWorkItemStatus({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      workItemId: params.id,
      actorUserId: auth.profile.id,
      operation: "complete",
      note: parsed.data.note ?? "Completed from workbench"
    });
    return ok(updated);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "work_item_complete_failed", 500);
  }
}
