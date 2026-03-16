import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { trackSuggestionAdoption } from "@/services/suggestion-adoption-service";
import { generateTaskActionSuggestion } from "@/services/task-action-service";
import { getWorkItemById } from "@/services/work-item-service";

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
      return fail("Sales can only view own task suggestions", 403);
    }

    const result = await generateTaskActionSuggestion({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: item.ownerId,
      workItemId: params.id
    });

    await trackSuggestionAdoption({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id,
      targetType: "task_action_suggestion",
      targetId: params.id,
      adoptionType: "viewed",
      adoptionContext: "during_task_execution"
    });

    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "task_action_suggestion_failed", 500);
  }
}
