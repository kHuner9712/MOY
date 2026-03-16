import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { compileUserMemory } from "@/services/memory-compile-service";

const requestSchema = z.object({
  userId: z.string().uuid().optional(),
  sourceWindowDays: z.number().int().min(7).max(180).optional()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const payload = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(payload);
  if (!parsed.success) return fail("Invalid request payload", 400);

  const targetUserId = parsed.data.userId ?? auth.profile.id;
  if (!isManager(auth.profile) && targetUserId !== auth.profile.id) {
    return fail("Sales can only refresh personal memory", 403);
  }

  try {
    const result = await compileUserMemory({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: targetUserId,
      triggeredByUserId: auth.profile.id,
      triggerSource: isManager(auth.profile) ? "manager_review" : "manual",
      sourceWindowDays: parsed.data.sourceWindowDays
    });

    return ok({
      profile: result.profile,
      items: result.items,
      usedFallback: result.usedFallback,
      fallbackReason: result.fallbackReason
    });
  } catch (error) {
    return fail(error instanceof Error ? error.message : "memory_refresh_failed", 500);
  }
}
