import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { generateContentDraft } from "@/services/content-draft-service";
import { trackSuggestionAdoption } from "@/services/suggestion-adoption-service";

const requestSchema = z.object({
  draftType: z.enum([
    "followup_message",
    "quote_explanation",
    "meeting_opening",
    "meeting_summary",
    "manager_checkin_note",
    "internal_update"
  ]),
  customerId: z.string().uuid().optional(),
  opportunityId: z.string().uuid().optional(),
  prepCardId: z.string().uuid().optional(),
  workItemId: z.string().uuid().optional(),
  title: z.string().max(120).optional()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    if (!isManager(auth.profile) && parsed.data.customerId) {
      const customerCheck = await auth.supabase
        .from("customers")
        .select("owner_id")
        .eq("org_id", auth.profile.org_id)
        .eq("id", parsed.data.customerId)
        .maybeSingle();
      if (customerCheck.error) throw new Error(customerCheck.error.message);
      const customerOwnerId = (customerCheck.data as { owner_id: string } | null)?.owner_id ?? null;
      if (!customerOwnerId || customerOwnerId !== auth.profile.id) return fail("No permission for this customer", 403);
    }

    if (!isManager(auth.profile) && parsed.data.workItemId) {
      const taskCheck = await auth.supabase
        .from("work_items")
        .select("owner_id")
        .eq("org_id", auth.profile.org_id)
        .eq("id", parsed.data.workItemId)
        .maybeSingle();
      if (taskCheck.error) throw new Error(taskCheck.error.message);
      const taskOwnerId = (taskCheck.data as { owner_id: string } | null)?.owner_id ?? null;
      if (!taskOwnerId || taskOwnerId !== auth.profile.id) return fail("No permission for this task", 403);
    }

    const result = await generateContentDraft({
      supabase: auth.supabase,
      profile: auth.profile,
      draftType: parsed.data.draftType,
      customerId: parsed.data.customerId ?? null,
      opportunityId: parsed.data.opportunityId ?? null,
      prepCardId: parsed.data.prepCardId ?? null,
      workItemId: parsed.data.workItemId ?? null,
      title: parsed.data.title ?? null
    });

    const adoptionContext =
      parsed.data.draftType === "quote_explanation"
        ? "before_quote"
        : parsed.data.draftType === "meeting_opening" || parsed.data.draftType === "meeting_summary"
          ? "before_meeting"
          : parsed.data.draftType === "followup_message"
            ? "before_followup"
            : "during_task_execution";

    await trackSuggestionAdoption({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id,
      targetType: "content_draft",
      targetId: result.draft.id,
      adoptionType: "viewed",
      adoptionContext
    }).catch(() => null);

    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "generate_content_draft_failed", 500);
  }
}
