import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { syncMobileDraft } from "@/services/mobile-sync-service";

const schema = z.object({
  localDraftId: z.string().min(1),
  draftType: z.enum(["capture", "outcome", "email_draft", "touchpoint_note"]),
  summary: z.string().optional(),
  payload: z.record(z.unknown()).default({})
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = schema.safeParse(body);
  if (!parsed.success) return fail("Invalid sync payload", 400);

  try {
    const result = await syncMobileDraft({
      supabase: auth.supabase,
      profile: auth.profile,
      localDraftId: parsed.data.localDraftId,
      draftType: parsed.data.draftType,
      summary: parsed.data.summary ?? null,
      payload: parsed.data.payload
    });
    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "mobile_draft_sync_failed", 500);
  }
}
