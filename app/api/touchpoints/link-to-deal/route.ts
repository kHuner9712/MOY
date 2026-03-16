import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { linkTouchpointToDeal } from "@/services/external-touchpoint-service";

const requestSchema = z.object({
  targetType: z.enum(["email_thread", "calendar_event", "document_asset"]),
  targetId: z.string().uuid(),
  customerId: z.string().uuid().optional(),
  opportunityId: z.string().uuid().optional(),
  dealRoomId: z.string().uuid().optional()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    const targetTable =
      parsed.data.targetType === "email_thread"
        ? "email_threads"
        : parsed.data.targetType === "calendar_event"
          ? "calendar_events"
          : "document_assets";

    const ownerRes = await auth.supabase
      .from(targetTable)
      .select("owner_id")
      .eq("org_id", auth.profile.org_id)
      .eq("id", parsed.data.targetId)
      .maybeSingle();
    if (ownerRes.error) throw new Error(ownerRes.error.message);
    const ownerId = (ownerRes.data as { owner_id: string } | null)?.owner_id ?? null;
    if (!ownerId) return fail("Touchpoint target not found", 404);
    if (!isManager(auth.profile) && ownerId !== auth.profile.id) return fail("Sales can only link own touchpoints", 403);

    await linkTouchpointToDeal({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      targetType: parsed.data.targetType,
      targetId: parsed.data.targetId,
      customerId: parsed.data.customerId ?? null,
      opportunityId: parsed.data.opportunityId ?? null,
      dealRoomId: parsed.data.dealRoomId ?? null,
      actorUserId: auth.profile.id
    });

    return ok({
      linked: true
    });
  } catch (error) {
    return fail(error instanceof Error ? error.message : "link_touchpoint_to_deal_failed", 500);
  }
}
