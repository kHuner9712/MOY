import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { generateCoachingReport } from "@/services/coaching-report-service";

const requestSchema = z.object({
  scope: z.enum(["user", "team"]),
  periodType: z.enum(["daily", "weekly", "monthly"]).optional(),
  targetUserId: z.string().uuid().nullable().optional()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const payload = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(payload);
  if (!parsed.success) return fail("Invalid request payload", 400);

  const input = parsed.data;

  if (!isManager(auth.profile)) {
    if (input.scope !== "user") {
      return fail("Sales can only generate personal coaching report", 403);
    }
    if (input.targetUserId && input.targetUserId !== auth.profile.id) {
      return fail("Sales cannot generate report for others", 403);
    }
  }

  try {
    const report = await generateCoachingReport({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      actorUserId: auth.profile.id,
      actorRole: auth.profile.role,
      scope: input.scope,
      periodType: input.periodType,
      targetUserId: input.targetUserId
    });
    return ok(report);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "coaching_report_generate_failed", 500);
  }
}
