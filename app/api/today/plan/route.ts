import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { getTodayPlanView } from "@/services/work-plan-service";
import { listWorkItems } from "@/services/work-item-service";

const querySchema = z.object({
  date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  customerId: z.string().uuid().optional(),
  sourceRefType: z.string().optional(),
  sourceRefIds: z.string().optional()
});

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const url = new URL(request.url);
  const parsed = querySchema.safeParse({
    date: url.searchParams.get("date") ?? undefined,
    customerId: url.searchParams.get("customerId") ?? undefined,
    sourceRefType: url.searchParams.get("sourceRefType") ?? undefined,
    sourceRefIds: url.searchParams.get("sourceRefIds") ?? undefined
  });
  if (!parsed.success) return fail("Invalid query", 400);

  const { date, customerId, sourceRefType, sourceRefIds } = parsed.data;

  try {
    if (customerId) {
      const items = await listWorkItems({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        customerId,
        statuses: ["todo", "in_progress", "snoozed", "done"],
        limit: 100
      });
      return ok({ workItems: items });
    }

    if (sourceRefType && sourceRefIds) {
      const ids = sourceRefIds.split(",").map((item) => item.trim()).filter(Boolean);
      if (ids.length === 0) return ok({ coverage: {} });

      const { data, error } = await auth.supabase
        .from("work_items")
        .select("source_ref_id, status")
        .eq("org_id", auth.profile.org_id)
        .eq("source_ref_type", sourceRefType)
        .in("source_ref_id", ids)
        .in("status", ["todo", "in_progress", "snoozed", "done"]);

      if (error) throw new Error(error.message);

      const coverage: Record<string, boolean> = {};
      for (const id of ids) coverage[id] = false;
      const rows = (data ?? []) as Array<{ source_ref_id: string | null }>;
      for (const row of rows) {
        if (row.source_ref_id) coverage[row.source_ref_id] = true;
      }
      return ok({ coverage });
    }

    const view = await getTodayPlanView({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id,
      date
    });
    return ok(view);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "get_today_plan_failed", 500);
  }
}
