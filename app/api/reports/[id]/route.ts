import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { getReportById } from "@/services/report-generation-service";

export async function GET(_request: Request, context: { params: { id: string } }) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const reportId = context.params.id;
  if (!reportId) return fail("Missing report id", 400);

  try {
    const report = await getReportById({
      supabase: auth.supabase,
      profile: auth.profile,
      reportId
    });

    if (!report) return fail("Report not found", 404);
    return ok(report);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "Get report failed", 500);
  }
}
