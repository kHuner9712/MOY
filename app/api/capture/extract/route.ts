import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { extractCommunicationInput } from "@/services/communication-extraction-service";

const requestSchema = z.object({
  sourceType: z.enum(["manual_note", "pasted_chat", "call_summary", "meeting_note", "voice_transcript", "imported_text"]),
  title: z.string().optional(),
  rawContent: z.string().min(5),
  customerId: z.string().uuid().nullable().optional(),
  inputLanguage: z.string().optional(),
  occurredAt: z.string().optional()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const payload = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(payload);
  if (!parsed.success) return fail("Invalid request payload", 400);

  const data = parsed.data;

  if (data.customerId) {
    const { data: customerRaw, error } = await auth.supabase
      .from("customers")
      .select("id, owner_id, org_id")
      .eq("id", data.customerId)
      .maybeSingle();

    const customer = customerRaw as { id: string; owner_id: string; org_id: string } | null;
    if (error) return fail(error.message, 500);
    if (!customer) return fail("Customer not found", 404);
    if (customer.org_id !== auth.profile.org_id) return fail("Cross-org access denied", 403);
    if (!isManager(auth.profile) && customer.owner_id !== auth.profile.id) return fail("Sales can only capture for owned customers", 403);
  }

  try {
    const result = await extractCommunicationInput({
      supabase: auth.supabase,
      profile: {
        id: auth.profile.id,
        org_id: auth.profile.org_id,
        role: auth.profile.role
      },
      sourceType: data.sourceType,
      title: data.title,
      rawContent: data.rawContent,
      customerId: data.customerId,
      inputLanguage: data.inputLanguage,
      occurredAt: data.occurredAt
    });

    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "Capture extraction failed", 500);
  }
}
