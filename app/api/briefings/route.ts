import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { mapDraftCoverageByWorkItem } from "@/lib/briefing-hub";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { getBriefingHubData } from "@/services/briefing-hub-service";
import { listContentDrafts } from "@/services/content-draft-service";
import { getPrepCoverageForWorkItems, listPrepCards } from "@/services/prep-card-service";
import { trackSuggestionAdoption } from "@/services/suggestion-adoption-service";

const querySchema = z.object({
  date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  customerId: z.string().uuid().optional(),
  workItemIds: z.string().optional(),
  limit: z.coerce.number().int().min(1).max(200).optional()
});

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const url = new URL(request.url);
  const parsed = querySchema.safeParse({
    date: url.searchParams.get("date") ?? undefined,
    customerId: url.searchParams.get("customerId") ?? undefined,
    workItemIds: url.searchParams.get("workItemIds") ?? undefined,
    limit: url.searchParams.get("limit") ?? undefined
  });
  if (!parsed.success) return fail("Invalid query", 400);

  const { date, customerId, workItemIds, limit } = parsed.data;

  try {
    const trackViewed = async (
      targetType: "prep_card" | "content_draft" | "morning_brief",
      targetId: string,
      adoptionContext: "before_followup" | "during_task_execution" | "after_review"
    ) => {
      await trackSuggestionAdoption({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        userId: auth.profile.id,
        targetType,
        targetId,
        adoptionType: "viewed",
        adoptionContext
      }).catch(() => null);
    };

    if (customerId && !isManager(auth.profile)) {
      const customerCheck = await auth.supabase
        .from("customers")
        .select("owner_id")
        .eq("org_id", auth.profile.org_id)
        .eq("id", customerId)
        .maybeSingle();
      if (customerCheck.error) throw new Error(customerCheck.error.message);
      const customerOwnerId = (customerCheck.data as { owner_id: string } | null)?.owner_id ?? null;
      if (!customerOwnerId || customerOwnerId !== auth.profile.id) return fail("No permission for this customer", 403);
    }

    if (workItemIds) {
      const ids = workItemIds
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
      const prepByWorkItemId = await getPrepCoverageForWorkItems({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        workItemIds: ids
      });
      const drafts = await listContentDrafts({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        ownerId: isManager(auth.profile) ? undefined : auth.profile.id,
        limit: limit ?? 80
      });
      const draftByWorkItemId = mapDraftCoverageByWorkItem(drafts, ids);

      await Promise.all(
        Object.values(prepByWorkItemId).map((item) => trackViewed("prep_card", item.id, "during_task_execution"))
      );
      await Promise.all(
        Object.values(draftByWorkItemId)
          .flatMap((rows) => rows ?? [])
          .map((item) => trackViewed("content_draft", item.id, "during_task_execution"))
      );

      return ok({
        prepByWorkItemId,
        draftByWorkItemId
      });
    }

    if (customerId) {
      const [prepCards, contentDrafts] = await Promise.all([
        listPrepCards({
          supabase: auth.supabase,
          orgId: auth.profile.org_id,
          ownerId: isManager(auth.profile) ? undefined : auth.profile.id,
          customerId,
          limit: limit ?? 30
        }),
        listContentDrafts({
          supabase: auth.supabase,
          orgId: auth.profile.org_id,
          ownerId: isManager(auth.profile) ? undefined : auth.profile.id,
          customerId,
          limit: limit ?? 30
        })
      ]);

      await Promise.all(prepCards.map((item) => trackViewed("prep_card", item.id, "before_followup")));
      await Promise.all(contentDrafts.map((item) => trackViewed("content_draft", item.id, "before_followup")));

      return ok({
        prepCards,
        contentDrafts
      });
    }

    const hub = await getBriefingHubData({
      supabase: auth.supabase,
      profile: auth.profile,
      date,
      prepLimit: limit ?? 40,
      draftLimit: limit ?? 40
    });

    if (hub.morningBrief?.id) {
      await trackViewed("morning_brief", hub.morningBrief.id, "after_review");
    }
    await Promise.all(hub.prepCards.map((item) => trackViewed("prep_card", item.id, "after_review")));
    await Promise.all(hub.contentDrafts.map((item) => trackViewed("content_draft", item.id, "after_review")));

    return ok(hub);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "get_briefings_failed", 500);
  }
}
