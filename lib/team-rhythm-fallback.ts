import type { ManagerTeamRhythmInsightResult } from "@/types/ai";
import type { TeamRhythmUserRow } from "@/types/work";

export function buildFallbackTeamRhythmInsight(params: {
  rows: TeamRhythmUserRow[];
  overdueTasks: number;
  unattendedCriticalCustomers: string[];
}): ManagerTeamRhythmInsightResult {
  const supportRows = [...params.rows]
    .sort((a, b) => b.backlogScore - a.backlogScore)
    .slice(0, 3)
    .map((item) => ({
      user_id: item.userId,
      user_name: item.userName,
      reason: `待办堆积 ${item.backlogScore}，超期 ${item.overdueCount}，完成率 ${(item.completionRate * 100).toFixed(0)}%`,
      priority: item.backlogScore >= 12 || item.overdueRate > 0.35 ? ("high" as const) : ("medium" as const)
    }));

  const strongRows = [...params.rows]
    .sort((a, b) => b.completionRate - a.completionRate)
    .slice(0, 2)
    .map((item) => `${item.userName}：完成率 ${(item.completionRate * 100).toFixed(0)}%，超期率 ${(item.overdueRate * 100).toFixed(0)}%`);

  return {
    team_execution_summary: `本期任务超期 ${params.overdueTasks} 条，需重点关注关键客户承接与待办堆积。`,
    overdue_patterns: [
      params.overdueTasks > 0 ? "部分任务长期未闭环，建议按优先级清理超期项" : "超期压力可控，维持当前节奏",
      "报价与决策节点任务最容易被延误，建议固定上午优先处理"
    ],
    under_attended_critical_customers: params.unattendedCriticalCustomers,
    who_needs_support: supportRows,
    which_actions_should_be_prioritized: [
      "优先关闭 critical/high 风险任务",
      "将已超期任务在 24 小时内转为明确下一步动作",
      "对高价值客户建立固定二次跟进窗口"
    ],
    managerial_actions: strongRows.length > 0 ? [`复制优秀执行样式：${strongRows.join("；")}`] : ["梳理团队周计划并复盘执行差距"]
  };
}
