import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { summarizeDocumentAsset } from "@/services/document-asset-service";

const requestSchema = z.object({
  documentId: z.string().uuid()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    const ownershipRes = await auth.supabase
      .from("document_assets")
      .select("owner_id")
      .eq("org_id", auth.profile.org_id)
      .eq("id", parsed.data.documentId)
      .maybeSingle();
    if (ownershipRes.error) throw new Error(ownershipRes.error.message);
    const ownerId = (ownershipRes.data as { owner_id: string } | null)?.owner_id ?? null;
    if (!ownerId) return fail("Document not found", 404);
    if (!isManager(auth.profile) && ownerId !== auth.profile.id) return fail("Sales can only summarize own documents", 403);

    const result = await summarizeDocumentAsset({
      supabase: auth.supabase,
      profile: auth.profile,
      documentId: parsed.data.documentId
    });

    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "summarize_document_failed", 500);
  }
}
