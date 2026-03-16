import type { AlertItem } from "@/types/alert";
import type { WorkItemSourceType, WorkType } from "@/types/work";

export interface WorkItemFromAlertDraft {
  sourceType: WorkItemSourceType;
  workType: WorkType;
  title: string;
  description: string;
  rationale: string;
  sourceRefType: "alert";
  sourceRefId: string;
}

export function buildWorkItemDraftFromAlert(alert: AlertItem): WorkItemFromAlertDraft {
  const topEvidence = alert.evidence.slice(0, 2).join("；");
  return {
    sourceType: "alert",
    workType: "resolve_alert",
    title: `处理提醒：${alert.title}`,
    description: topEvidence ? `${alert.message}\n证据：${topEvidence}` : alert.message,
    rationale: `来源于 ${alert.source.toUpperCase()} 提醒，等级 ${alert.level}，不处理可能扩大漏单风险`,
    sourceRefType: "alert",
    sourceRefId: alert.id
  };
}
