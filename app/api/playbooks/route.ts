import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { listPlaybooks } from "@/services/playbook-service";

const querySchema = z.object({
  ownerUserId: z.string().uuid().optional(),
  scopeType: z.enum(["org", "team", "user"]).optional(),
  playbookType: z.enum(["objection_handling", "customer_segment", "quote_strategy", "meeting_strategy", "followup_rhythm", "risk_recovery"]).optional(),
  includeEntries: z.enum(["true", "false"]).optional(),
  limit: z.coerce.number().int().min(1).max(200).optional()
});

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const url = new URL(request.url);
  const parsed = querySchema.safeParse({
    ownerUserId: url.searchParams.get("ownerUserId") ?? undefined,
    scopeType: url.searchParams.get("scopeType") ?? undefined,
    playbookType: url.searchParams.get("playbookType") ?? undefined,
    includeEntries: url.searchParams.get("includeEntries") ?? undefined,
    limit: url.searchParams.get("limit") ?? undefined
  });
  if (!parsed.success) return fail("Invalid query", 400);

  if (!isManager(auth.profile) && parsed.data.scopeType && parsed.data.scopeType !== "user") {
    return fail("Sales can only view personal playbooks", 403);
  }

  const ownerUserId = isManager(auth.profile) ? parsed.data.ownerUserId : auth.profile.id;

  try {
    const rows = await listPlaybooks({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      ownerUserId,
      scopeType: parsed.data.scopeType,
      playbookType: parsed.data.playbookType,
      statuses: ["active", "draft"],
      limit: parsed.data.limit ?? 60,
      includeEntries: parsed.data.includeEntries !== "false"
    });
    return ok(rows);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "list_playbooks_failed", 500);
  }
}
