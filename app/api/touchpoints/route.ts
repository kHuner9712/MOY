import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { getTouchpointHubView, touchpointEventSummary } from "@/services/external-touchpoint-service";
import type { TouchpointHubView } from "@/types/touchpoint";

const querySchema = z.object({
  ownerId: z.string().uuid().optional(),
  customerId: z.string().uuid().optional(),
  dealRoomId: z.string().uuid().optional(),
  type: z.enum(["email", "meeting", "document"]).optional(),
  limit: z.coerce.number().int().min(1).max(200).optional()
});

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const url = new URL(request.url);
  const parsed = querySchema.safeParse({
    ownerId: url.searchParams.get("ownerId") ?? undefined,
    customerId: url.searchParams.get("customerId") ?? undefined,
    dealRoomId: url.searchParams.get("dealRoomId") ?? undefined,
    type: url.searchParams.get("type") ?? undefined,
    limit: url.searchParams.get("limit") ?? undefined
  });
  if (!parsed.success) return fail("Invalid query", 400);

  const ownerId = isManager(auth.profile) ? parsed.data.ownerId : auth.profile.id;
  if (!isManager(auth.profile) && parsed.data.ownerId && parsed.data.ownerId !== auth.profile.id) {
    return fail("Sales can only access own touchpoints", 403);
  }

  try {
    if (!isManager(auth.profile) && parsed.data.customerId) {
      const customerRes = await auth.supabase
        .from("customers")
        .select("owner_id")
        .eq("org_id", auth.profile.org_id)
        .eq("id", parsed.data.customerId)
        .maybeSingle();
      if (customerRes.error) throw new Error(customerRes.error.message);
      const customerOwnerId = (customerRes.data as { owner_id: string } | null)?.owner_id ?? null;
      if (!customerOwnerId || customerOwnerId !== auth.profile.id) {
        return fail("No permission for this customer", 403);
      }
    }

    const [hubRaw, summary] = await Promise.all([
      getTouchpointHubView({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        ownerId,
        customerId: parsed.data.customerId,
        dealRoomId: parsed.data.dealRoomId,
        limit: parsed.data.limit
      }),
      touchpointEventSummary({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        ownerId,
        sinceDays: 7
      })
    ]);

    let hub: TouchpointHubView = hubRaw;
    if (parsed.data.type === "email") {
      hub = {
        ...hubRaw,
        calendarEvents: [],
        documentAssets: [],
        events: hubRaw.events.filter((item) => item.touchpointType === "email")
      };
    } else if (parsed.data.type === "meeting") {
      hub = {
        ...hubRaw,
        emailThreads: [],
        documentAssets: [],
        events: hubRaw.events.filter((item) => item.touchpointType === "meeting")
      };
    } else if (parsed.data.type === "document") {
      hub = {
        ...hubRaw,
        emailThreads: [],
        calendarEvents: [],
        events: hubRaw.events.filter((item) => item.touchpointType === "document")
      };
    }

    return ok({
      hub,
      summary
    });
  } catch (error) {
    return fail(error instanceof Error ? error.message : "get_touchpoints_failed", 500);
  }
}
