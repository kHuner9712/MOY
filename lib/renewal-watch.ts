import type { RenewalWatchItem } from "@/types/automation";

export function inferRenewalStatus(params: {
  healthBand: "healthy" | "watch" | "at_risk" | "critical";
  overallHealthScore: number;
  renewalDueAt: string | null;
}): RenewalWatchItem["renewalStatus"] {
  if (params.renewalDueAt) {
    const dueTs = new Date(params.renewalDueAt).getTime();
    if (dueTs < Date.now() + 15 * 24 * 60 * 60 * 1000) {
      if (params.healthBand === "at_risk" || params.healthBand === "critical") return "at_risk";
      return "due_soon";
    }
  }
  if (params.healthBand === "critical" || params.healthBand === "at_risk") return "at_risk";
  if (params.overallHealthScore >= 78) return "expansion_candidate";
  if (params.healthBand === "watch") return "watch";
  return "watch";
}

