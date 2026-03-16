import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { listDealRooms } from "@/services/deal-room-service";

const querySchema = z.object({
  status: z.string().optional(),
  priorityBand: z.string().optional(),
  ownerId: z.string().uuid().optional(),
  managerAttentionNeeded: z.enum(["true", "false"]).optional(),
  limit: z.string().optional()
});

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const url = new URL(request.url);
  const parsed = querySchema.safeParse({
    status: url.searchParams.get("status") ?? undefined,
    priorityBand: url.searchParams.get("priorityBand") ?? undefined,
    ownerId: url.searchParams.get("ownerId") ?? undefined,
    managerAttentionNeeded: url.searchParams.get("managerAttentionNeeded") ?? undefined,
    limit: url.searchParams.get("limit") ?? undefined
  });
  if (!parsed.success) return fail("Invalid query", 400);

  const statuses = parsed.data.status?.split(",").map((item) => item.trim()).filter(Boolean) as
    | ("active" | "watchlist" | "escalated" | "blocked" | "won" | "lost" | "archived")[]
    | undefined;
  const priorityBands = parsed.data.priorityBand?.split(",").map((item) => item.trim()).filter(Boolean) as
    | ("normal" | "important" | "strategic" | "critical")[]
    | undefined;

  if (!isManager(auth.profile) && parsed.data.ownerId && parsed.data.ownerId !== auth.profile.id) {
    return fail("Sales can only query own deal rooms", 403);
  }

  try {
    const rooms = await listDealRooms({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      ownerId: isManager(auth.profile) ? parsed.data.ownerId : auth.profile.id,
      statuses,
      priorityBands,
      managerAttentionNeeded: parsed.data.managerAttentionNeeded ? parsed.data.managerAttentionNeeded === "true" : undefined,
      limit: parsed.data.limit ? Number(parsed.data.limit) : 100
    });
    return ok(rooms);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "list_deals_failed", 500);
  }
}

