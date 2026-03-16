import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { getMobileTouchpointView } from "@/services/mobile-touchpoint-service";

const querySchema = z.object({
  customerId: z.string().uuid().optional(),
  dealRoomId: z.string().uuid().optional()
});

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const url = new URL(request.url);
  const parsed = querySchema.safeParse({
    customerId: url.searchParams.get("customerId") ?? undefined,
    dealRoomId: url.searchParams.get("dealRoomId") ?? undefined
  });
  if (!parsed.success) return fail("Invalid query", 400);

  try {
    const result = await getMobileTouchpointView({
      supabase: auth.supabase,
      profile: auth.profile,
      customerId: parsed.data.customerId,
      dealRoomId: parsed.data.dealRoomId
    });
    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "mobile_touchpoints_failed", 500);
  }
}
