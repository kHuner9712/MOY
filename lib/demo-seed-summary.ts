export interface DemoSeedStepLike {
  name: string;
  success: boolean;
  inserted: number;
  message: string;
}

export function summarizeDemoSeedSteps(steps: DemoSeedStepLike[]): {
  status: "completed" | "failed";
  partialSuccess: boolean;
  summary: string;
} {
  const failed = steps.filter((step) => !step.success);
  const partialSuccess = failed.length > 0;
  if (partialSuccess) {
    return {
      status: "failed",
      partialSuccess: true,
      summary: `Demo seed partial success: ${steps.length - failed.length}/${steps.length} steps completed`
    };
  }

  return {
    status: "completed",
    partialSuccess: false,
    summary: "Demo seed completed"
  };
}
