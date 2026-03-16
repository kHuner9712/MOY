export interface ImportExecutionOutcome {
  jobStatus: "completed" | "failed";
  partialSuccess: boolean;
}

export function resolveImportExecutionOutcome(params: {
  importedRows: number;
  errorRows: number;
}): ImportExecutionOutcome {
  const imported = Math.max(0, Math.trunc(params.importedRows));
  const failed = Math.max(0, Math.trunc(params.errorRows));

  if (imported === 0 && failed > 0) {
    return {
      jobStatus: "failed",
      partialSuccess: false
    };
  }

  return {
    jobStatus: "completed",
    partialSuccess: imported > 0 && failed > 0
  };
}

