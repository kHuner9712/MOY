import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { listReports } from "@/services/report-generation-service";
import type { ReportType } from "@/types/report";

const reportTypes: ReportType[] = ["sales_daily", "sales_weekly", "manager_daily", "manager_weekly"];

function asReportType(value: string | null): ReportType | undefined {
  if (!value) return undefined;
  return reportTypes.includes(value as ReportType) ? (value as ReportType) : undefined;
}

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const { searchParams } = new URL(request.url);
  const reportType = asReportType(searchParams.get("reportType"));
  const from = searchParams.get("from") ?? undefined;
  const to = searchParams.get("to") ?? undefined;
  const limitRaw = Number(searchParams.get("limit") ?? "40");
  const limit = Number.isFinite(limitRaw) ? Math.max(1, Math.min(80, limitRaw)) : 40;

  try {
    const reports = await listReports({
      supabase: auth.supabase,
      profile: auth.profile,
      reportType,
      from,
      to,
      limit
    });

    return ok(reports);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "List reports failed", 500);
  }
}
