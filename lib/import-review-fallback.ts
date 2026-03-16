export function buildFallbackImportReviewSummary(params: {
  importType: string;
  totalRows: number;
  importedRows: number;
  invalidRows: number;
  duplicateRows: number;
  mergedRows: number;
  skippedRows: number;
  commonErrors: string[];
}): {
  summary: string;
  issues: string[];
  recommended_cleanup: string[];
  recommended_next_steps: string[];
} {
  const successRate = params.totalRows > 0 ? Math.round((params.importedRows / params.totalRows) * 100) : 0;
  const issues: string[] = [];

  if (params.invalidRows > 0) issues.push(`${params.invalidRows} rows failed validation.`);
  if (params.duplicateRows > 0) issues.push(`${params.duplicateRows} rows are duplicate/merge candidates.`);
  if (params.skippedRows > 0) issues.push(`${params.skippedRows} rows were skipped.`);
  if (params.commonErrors.length > 0) {
    issues.push(`Common errors: ${params.commonErrors.slice(0, 3).join("; ")}`);
  }

  return {
    summary: `${params.importType} import completed with ${successRate}% effective import (${params.importedRows}/${params.totalRows}).`,
    issues,
    recommended_cleanup: [
      "Review invalid rows and fix missing owner/stage/date values.",
      "Resolve duplicate candidates using merge or create-new decision.",
      "Save validated column mapping as import template for next batch."
    ],
    recommended_next_steps: [
      "Run /today plan to generate first actionable tasks for imported customers.",
      "Open /manager view to verify risk distribution after import.",
      "Create at least one deal room for high-value imported opportunities."
    ]
  };
}

