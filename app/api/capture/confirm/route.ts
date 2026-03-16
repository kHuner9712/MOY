import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { confirmCommunicationInput } from "@/services/communication-extraction-service";
import { completeWorkItemsBySourceRef } from "@/services/work-item-service";

const requestSchema = z.object({
  inputId: z.string().uuid(),
  customerId: z.string().uuid().nullable().optional()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const payload = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(payload);
  if (!parsed.success) return fail("Invalid request payload", 400);

  const { inputId, customerId } = parsed.data;

  const { data: inputRowRaw, error: inputError } = await auth.supabase
    .from("communication_inputs")
    .select("id, org_id, owner_id, created_by")
    .eq("id", inputId)
    .maybeSingle();

  const inputRow = inputRowRaw as { id: string; org_id: string; owner_id: string; created_by: string } | null;
  if (inputError) return fail(inputError.message, 500);
  if (!inputRow) return fail("Capture input not found", 404);
  if (inputRow.org_id !== auth.profile.org_id) return fail("Cross-org access denied", 403);
  if (!isManager(auth.profile) && inputRow.owner_id !== auth.profile.id && inputRow.created_by !== auth.profile.id) {
    return fail("No permission to confirm this capture input", 403);
  }

  if (customerId) {
    const { data: customerRaw, error: customerError } = await auth.supabase
      .from("customers")
      .select("id, owner_id, org_id")
      .eq("id", customerId)
      .maybeSingle();
    const customer = customerRaw as { id: string; owner_id: string; org_id: string } | null;

    if (customerError) return fail(customerError.message, 500);
    if (!customer) return fail("Customer not found", 404);
    if (customer.org_id !== auth.profile.org_id) return fail("Cross-org access denied", 403);
    if (!isManager(auth.profile) && customer.owner_id !== auth.profile.id) {
      return fail("Sales can only confirm for owned customers", 403);
    }
  }

  try {
    const result = await confirmCommunicationInput({
      supabase: auth.supabase,
      profile: {
        id: auth.profile.id,
        org_id: auth.profile.org_id,
        role: auth.profile.role
      },
      inputId,
      customerId
    });

    if (result.status === "confirmed" && result.followupId) {
      await completeWorkItemsBySourceRef({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        actorUserId: auth.profile.id,
        sourceRefType: "followup_draft",
        sourceRefId: result.followupId,
        note: "Auto completed after capture draft confirmed"
      });
    }

    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "Capture confirm failed", 500);
  }
}
