import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { runMobileQuickCaptureRefine } from "@/services/mobile-sync-service";

const schema = z.object({
  rawInput: z.string().min(1),
  customerId: z.string().uuid().optional(),
  dealRoomId: z.string().uuid().optional()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);
  const body = await request.json().catch(() => null);
  const parsed = schema.safeParse(body);
  if (!parsed.success) return fail("Invalid refine payload", 400);

  try {
    const result = await runMobileQuickCaptureRefine({
      supabase: auth.supabase,
      profile: auth.profile,
      rawInput: parsed.data.rawInput,
      customerId: parsed.data.customerId ?? null,
      dealRoomId: parsed.data.dealRoomId ?? null
    });
    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "mobile_capture_refine_failed", 500);
  }
}
