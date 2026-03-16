export type ReportType = "sales_daily" | "sales_weekly" | "manager_daily" | "manager_weekly";
export type ReportScopeType = "self" | "team" | "org";
export type ReportStatus = "generating" | "completed" | "failed";

export interface GeneratedReport {
  id: string;
  orgId: string;
  reportType: ReportType;
  targetUserId: string | null;
  scopeType: ReportScopeType;
  periodStart: string;
  periodEnd: string;
  status: ReportStatus;
  title: string;
  summary: string;
  contentMarkdown: string;
  metricsSnapshot: Record<string, unknown>;
  sourceSnapshot: Record<string, unknown>;
  generatedBy: string;
  createdAt: string;
  updatedAt: string;
}

export interface GenerateReportInput {
  reportType: ReportType;
  periodStart: string;
  periodEnd: string;
  targetUserId?: string | null;
  scopeType?: ReportScopeType;
}
