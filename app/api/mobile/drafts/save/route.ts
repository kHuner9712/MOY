import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { createMobileDraftSyncJob } from "@/services/mobile-draft-service";

const schema = z.object({
  localDraftId: z.string().min(1),
  draftType: z.enum(["capture", "outcome", "email_draft", "touchpoint_note"]),
  summary: z.string().optional(),
  payload: z.record(z.unknown()).default({}),
  targetEntityType: z.string().optional(),
  targetEntityId: z.string().uuid().optional()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = schema.safeParse(body);
  if (!parsed.success) return fail("Invalid draft payload", 400);

  try {
    const job = await createMobileDraftSyncJob({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id,
      localDraftId: parsed.data.localDraftId,
      draftType: parsed.data.draftType,
      summary: parsed.data.summary ?? null,
      payloadSnapshot: parsed.data.payload,
      targetEntityType: parsed.data.targetEntityType ?? null,
      targetEntityId: parsed.data.targetEntityId ?? null,
      syncStatus: "pending"
    });
    return ok({
      job
    });
  } catch (error) {
    return fail(error instanceof Error ? error.message : "mobile_draft_save_failed", 500);
  }
}
