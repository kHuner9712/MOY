import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { assertOrgManagerAccess } from "@/services/org-membership-service";
import { recommendTemplateFit } from "@/services/template-fit-service";

const requestSchema = z.object({
  industryHint: z.string().trim().max(200).nullable().optional()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const payload = await request.json().catch(() => ({}));
  const parsed = requestSchema.safeParse(payload);
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    await assertOrgManagerAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const result = await recommendTemplateFit({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      actorUserId: auth.profile.id,
      industryHint: parsed.data.industryHint ?? null
    });

    return ok(result);
  } catch (error) {
    const message = error instanceof Error ? error.message : "template_recommend_failed";
    const status = message === "org_manager_access_required" ? 403 : 500;
    return fail(message, status);
  }
}

