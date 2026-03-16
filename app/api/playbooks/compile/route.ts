import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { compilePlaybook } from "@/services/playbook-compile-service";

const requestSchema = z.object({
  scopeType: z.enum(["org", "team", "user"]).optional(),
  ownerUserId: z.string().uuid().nullable().optional(),
  periodStart: z.string().optional(),
  periodEnd: z.string().optional(),
  title: z.string().max(200).optional()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  const scopeType = parsed.data.scopeType ?? (auth.profile.role === "manager" ? "team" : "user");

  if (!isManager(auth.profile) && scopeType !== "user") {
    return fail("Sales can only compile personal playbooks", 403);
  }

  if (!isManager(auth.profile) && parsed.data.ownerUserId && parsed.data.ownerUserId !== auth.profile.id) {
    return fail("Sales can only compile own playbooks", 403);
  }

  try {
    const result = await compilePlaybook({
      supabase: auth.supabase,
      profile: auth.profile,
      scopeType,
      ownerUserId: parsed.data.ownerUserId ?? (scopeType === "user" ? auth.profile.id : null),
      periodStart: parsed.data.periodStart,
      periodEnd: parsed.data.periodEnd,
      title: parsed.data.title
    });

    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "playbook_compile_failed", 500);
  }
}
