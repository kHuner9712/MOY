import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { generateEmailDraft } from "@/services/email-draft-service";

const requestSchema = z.object({
  contextType: z.enum(["followup", "quote", "meeting_confirm", "meeting_followup", "manager_support"]),
  customerId: z.string().uuid().optional(),
  opportunityId: z.string().uuid().optional(),
  dealRoomId: z.string().uuid().optional(),
  threadId: z.string().uuid().optional(),
  extraInstruction: z.string().max(2000).optional()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  const payload = parsed.data;

  try {
    if (!isManager(auth.profile) && payload.customerId) {
      const customerRes = await auth.supabase
        .from("customers")
        .select("owner_id")
        .eq("org_id", auth.profile.org_id)
        .eq("id", payload.customerId)
        .maybeSingle();
      if (customerRes.error) throw new Error(customerRes.error.message);
      const customerOwnerId = (customerRes.data as { owner_id: string } | null)?.owner_id ?? null;
      if (!customerOwnerId || customerOwnerId !== auth.profile.id) {
        return fail("No permission for this customer", 403);
      }
    }

    const result = await generateEmailDraft({
      supabase: auth.supabase,
      profile: auth.profile,
      contextType: payload.contextType,
      customerId: payload.customerId ?? null,
      opportunityId: payload.opportunityId ?? null,
      dealRoomId: payload.dealRoomId ?? null,
      threadId: payload.threadId ?? null,
      extraInstruction: payload.extraInstruction ?? null
    });

    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "generate_email_draft_failed", 500);
  }
}
