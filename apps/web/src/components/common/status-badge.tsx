import * as React from 'react';

import { Badge, type BadgeProps } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

/**
 * 状态 → 业务文案 + 颜色等级。
 *
 * 铁律（PRD 第 7 章术语对照表）：
 * - 文案只用业务语言（解析中 / 可使用 / 处理失败 / 指定成员可见…），
 *   不出现任何处理参数、技术名词或 5 位码值；
 * - 颜色只用设计令牌与既有徽标变体，不新增色值。
 */
export type StatusTone = 'success' | 'warning' | 'danger' | 'info' | 'neutral';

/** 状态描述：文案 + 色调 */
export interface StatusDescriptor {
  /** 用户可见文案 */
  label: string;
  /** 颜色等级 */
  tone: StatusTone;
}

/** 未知状态的兜底文案（宁可含糊也不暴露内部取值） */
export const UNKNOWN_STATUS_LABEL = '待确认';

/** 状态映射表：键为接口返回的枚举值（小写下划线），值为业务文案 */
const STATUS_DESCRIPTORS: Readonly<Record<string, StatusDescriptor>> = Object.freeze({
  // 文档处理进度（三态）
  processing: { label: '解析中', tone: 'warning' },
  completed: { label: '可使用', tone: 'success' },
  failed: { label: '处理失败', tone: 'danger' },

  // 文档处理阶段（五态，与三态语义对齐）
  uploaded: { label: '已上传', tone: 'neutral' },
  parsing: { label: '正在读取内容', tone: 'warning' },
  preparing: { label: '正在准备内容', tone: 'warning' },

  // 引用片段可定位状态
  valid: { label: '可定位', tone: 'success' },
  stale: { label: '旧版本内容', tone: 'warning' },

  // 知识库可见范围
  organization: { label: '企业可见', tone: 'info' },
  private: { label: '仅自己可见', tone: 'neutral' },
  restricted: { label: '指定成员可见', tone: 'info' },

  // 成员在册状态
  active: { label: '已加入', tone: 'success' },
  invited: { label: '待接受邀请', tone: 'warning' },
  disabled: { label: '已停用', tone: 'neutral' },

  // 工具连接状态
  connected: { label: '已连接', tone: 'success' },
  expired: { label: '待重新连接', tone: 'warning' },
  revoked: { label: '已断开', tone: 'neutral' },

  // 订单与支付
  pending_payment: { label: '待支付', tone: 'warning' },
  paid: { label: '已支付', tone: 'success' },
  closed: { label: '已关闭', tone: 'neutral' },
  cancelled: { label: '已取消', tone: 'neutral' },
  refunded: { label: '已退款', tone: 'neutral' },

  // 调用结果
  success: { label: '成功', tone: 'success' },

  // 账单状态
  issued: { label: '已开具', tone: 'success' },
  pending: { label: '处理中', tone: 'warning' },

  // 套餐上下架
  available: { label: '可购买', tone: 'success' },
  offline: { label: '已下架', tone: 'neutral' },
});

/** 状态色调 → 徽标变体 */
const TONE_VARIANTS: Readonly<Record<StatusTone, BadgeProps['variant']>> = Object.freeze({
  success: 'success',
  warning: 'warning',
  danger: 'destructive',
  info: 'secondary',
  neutral: 'outline',
});

/**
 * 取状态对应的业务文案与色调。
 *
 * @param status - 接口返回的状态值
 * @returns 文案与色调；未登记的状态返回「待确认」+ 中性色
 */
export function statusDescriptor(status: string): StatusDescriptor {
  return STATUS_DESCRIPTORS[status] ?? { label: UNKNOWN_STATUS_LABEL, tone: 'neutral' };
}

/** 状态徽标属性 */
export interface StatusBadgeProps {
  /** 接口返回的状态值 */
  status: string;
  /** 覆盖文案（如后端已直出 `stage_display_name`） */
  label?: string;
  className?: string;
}

/**
 * 状态徽标：把枚举值统一翻译成业务语言与颜色。
 *
 * @param props - 组件属性
 * @returns 徽标元素
 */
export function StatusBadge({ status, label, className }: StatusBadgeProps): React.JSX.Element {
  const descriptor = statusDescriptor(status);
  return (
    <Badge variant={TONE_VARIANTS[descriptor.tone]} className={cn('whitespace-nowrap', className)}>
      {label ?? descriptor.label}
    </Badge>
  );
}
