import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { uploadDocumentAsset } from "@/services/document-asset-service";

const requestSchema = z.object({
  ownerId: z.string().uuid().optional(),
  customerId: z.string().uuid().optional(),
  opportunityId: z.string().uuid().optional(),
  dealRoomId: z.string().uuid().optional(),
  sourceType: z.enum(["upload", "email_attachment", "generated", "imported"]).optional(),
  documentType: z.enum(["proposal", "quote", "contract_draft", "meeting_note", "case_study", "product_material", "other"]).optional(),
  title: z.string().min(1).max(300),
  fileName: z.string().min(1).max(260),
  mimeType: z.string().max(120).optional(),
  storagePath: z.string().max(400).optional(),
  extractedText: z.string().max(120000).optional(),
  tags: z.array(z.string().max(60)).optional(),
  linkedPrepCardId: z.string().uuid().optional(),
  linkedDraftId: z.string().uuid().optional(),
  autoSummarize: z.boolean().optional()
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
      if (!customerOwnerId || customerOwnerId !== auth.profile.id) return fail("No permission for this customer", 403);
    }

    const result = await uploadDocumentAsset({
      supabase: auth.supabase,
      profile: auth.profile,
      ownerId: isManager(auth.profile) ? payload.ownerId : auth.profile.id,
      customerId: payload.customerId ?? null,
      opportunityId: payload.opportunityId ?? null,
      dealRoomId: payload.dealRoomId ?? null,
      sourceType: payload.sourceType,
      documentType: payload.documentType,
      title: payload.title,
      fileName: payload.fileName,
      mimeType: payload.mimeType,
      storagePath: payload.storagePath ?? null,
      extractedText: payload.extractedText ?? "",
      tags: payload.tags ?? [],
      linkedPrepCardId: payload.linkedPrepCardId ?? null,
      linkedDraftId: payload.linkedDraftId ?? null,
      autoSummarize: payload.autoSummarize ?? true
    });

    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "upload_document_failed", 500);
  }
}
