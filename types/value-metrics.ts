/**
 * 价值指标类型定义
 * 基于现有数据结构，设计一组最小可用的"价值指标"
 * 所有指标都可从现有表推导，无需新增 migration
 */

export interface ValueMetrics {
  periodStart: string;
  periodEnd: string;
  lessFollowup: number;
  newRiskCustomers: number;
  handledRiskEvents: number;
  managerInterventions: number;
  recoveredProgressions: number;
  aiAdoptions: number;
  timeSavedMinutes: number;
  weeklyTrend: TrendResult;
}

export interface TrendResult {
  direction: 'up' | 'down' | 'stable';
  changePercent: number;
  description: string;
}

export interface ValueMetricsResult {
  current: ValueMetrics;
  previous: ValueMetrics | null;
  trend: TrendResult;
}

export interface ValueMetricsSummary {
  headline: string;
  highlights: string[];
  keyNumbers: Array<{
    label: string;
    value: number;
    trend: 'up' | 'down' | 'stable';
    change: number;
  }>;
  recommendation: string;
}

export interface ValueMetricsParams {
  orgId: string;
  ownerId?: string;
}

export interface KeyNumberItem {
  label: string;
  value: number;
  trend: 'up' | 'down' | 'stable';
  change: number;
  description?: string;
}

export interface ValueOverviewBlock {
  title: string;
  summary: string;
  metrics: KeyNumberItem[];
  trend: TrendResult;
  recommendation: string;
}

export interface TodayPriorityAction {
  workItemId: string;
  title: string;
  customerName?: string;
  priority: 'critical' | 'high' | 'medium' | 'low';
  actionType: 'reduce_risk' | 'recover_progression' | 'prevent_loss' | 'capture_opportunity';
  reason: string;
  expectedImpact: string;
  dueAt?: string;
}

export interface TodayValueSummary {
  completedTasks: number;
  aiAssistedTasks: number;
  riskActionsPending: number;
  progressionActionsPending: number;
  estimatedTimeSavedMinutes: number;
  priorityActions: TodayPriorityAction[];
}

export const VALUE_METRICS_LABELS: Record<string, string> = {
  lessFollowup: '漏跟进减少',
  newRiskCustomers: '新增风险客户',
  handledRiskEvents: '风险事件处理',
  managerInterventions: '经理介入',
  recoveredProgressions: '推进恢复',
  aiAdoptions: 'AI 建议采纳',
  timeSavedMinutes: '节省时间(分钟)'
};

export const VALUE_METRICS_DESCRIPTIONS: Record<string, string> = {
  lessFollowup: '本周通过系统预警，减少的漏跟进数量',
  newRiskCustomers: '本周新识别为风险状态的客户数',
  handledRiskEvents: '本周被系统识别并已处理的风险事件数',
  managerInterventions: '本周经理介入处理的事项数',
  recoveredProgressions: '本周从停滞状态恢复推进的客户/商机数',
  aiAdoptions: '本周 AI 建议被采纳（复制/直接使用）的次数',
  timeSavedMinutes: '本周节省的人工整理/提醒/汇总时间（分钟）'
};

export const ACTION_TYPE_LABELS: Record<TodayPriorityAction['actionType'], string> = {
  reduce_risk: '降低风险',
  recover_progression: '恢复推进',
  prevent_loss: '防止流失',
  capture_opportunity: '抓住机会'
};

export const ACTION_TYPE_DESCRIPTIONS: Record<TodayPriorityAction['actionType'], string> = {
  reduce_risk: '处理风险客户，避免进一步恶化',
  recover_progression: '推动停滞客户恢复进展',
  prevent_loss: '防止高价值客户流失',
  capture_opportunity: '抓住转化机会，推进成交'
};
