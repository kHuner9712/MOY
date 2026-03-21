import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { canViewManagerWorkspace } from "@/lib/role-capability";
import { getServerAuthContext } from "@/lib/server-auth";
import { generateReport } from "@/services/report-generation-service";

const requestSchema = z.object({
  reportType: z.enum(["sales_daily", "sales_weekly", "manager_daily", "manager_weekly"]),
  periodStart: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  periodEnd: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  targetUserId: z.string().uuid().nullable().optional(),
  scopeType: z.enum(["self", "team", "org"]).optional()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const payload = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(payload);
  if (!parsed.success) return fail("Invalid request payload", 400);

  const input = parsed.data;
  const canUseTeamScope = canViewManagerWorkspace({
    role: auth.profile.role,
    orgRole: auth.membership?.role
  });

  if (!canUseTeamScope) {
    if (input.reportType !== "sales_daily" && input.reportType !== "sales_weekly") {
      return fail("Sales can only generate personal sales reports", 403);
    }
    if (input.scopeType && input.scopeType !== "self") {
      return fail("Sales report scope must be self", 403);
    }
    if (input.targetUserId && input.targetUserId !== auth.profile.id) {
      return fail("Sales cannot generate reports for other users", 403);
    }
  }

  try {
    const report = await generateReport({
      supabase: auth.supabase,
      profile: auth.profile,
      input: {
        reportType: input.reportType,
        periodStart: input.periodStart,
        periodEnd: input.periodEnd,
        targetUserId: input.targetUserId,
        scopeType: input.scopeType ?? (canUseTeamScope ? "team" : "self")
      }
    });

    return ok(report);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "Generate report failed", 500);
  }
}
