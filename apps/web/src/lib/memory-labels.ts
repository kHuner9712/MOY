import type { MemoryCategory } from '@/lib/types';

/**
 * 「我的记忆」页的用户可见文案（**唯一来源**）。
 *
 * 为什么把文案抽出来集中放：
 * 1. 禁词扫描要能一眼看全（技术黑盒化是硬约束，散在 JSX 里最容易漏）；
 * 2. PRD §4.4 / §6.1 的文案是**合同条款**，改动必须集中、可审查；
 * 3. 空态有 6 套，散落在条件分支里迟早出现「该说的没说」。
 *
 * 严禁出现：向量 / embedding / 召回 / top-k / score / 相似度 / 阈值 / 索引 /
 * 记忆图谱 / 抽取器 / 模型名 / 租户 / 错误码。
 */

/** 类别 → 用户可见名（与 PRD §4.2「用户可见名」列一致） */
export const MEMORY_CATEGORY_LABELS: Readonly<Record<MemoryCategory, string>> = Object.freeze({
  identity: '称呼',
  org: '所在团队',
  preference: '工作习惯',
  convention: '常用说法',
  constraint: '注意事项',
  project: '项目背景',
});

/** 空态种类（6 套，每套文案不同） */
export type MemoryEmptyKind =
  /** 从未产生过（含"还没开始了解你"：信息采集尚未开始 / 尚未配置） */
  | 'never'
  /** 用户刚把所有信息删光了 */
  | 'cleared'
  /** 当前筛选条件下没有结果 */
  | 'filtered'
  /** 记忆功能已被关闭 */
  | 'disabled'
  /** 信息暂时没能取回（后端侧能力不畅，界面上不暴露原因） */
  | 'unavailable'
  /** 加载失败（可重试） */
  | 'failed';

/** 空态文案（每一条都要说清"没有什么 + 为什么 + 下一步"） */
export const MEMORY_EMPTY_COPY: Readonly<
  Record<MemoryEmptyKind, { title: string; description: string; actionLabel?: string }>
> = Object.freeze({
  never: {
    title: '我还不太了解你',
    description: '多聊几次，我会在这里记下能帮你省事的那些信息。',
    actionLabel: '去看对话',
  },
  cleared: {
    title: '你的记忆已全部删除',
    description: '之后我会重新开始了解你。',
  },
  filtered: {
    title: '这个类别下还没有信息',
    description: '换个类别看看，或者继续对话让我了解更多。',
  },
  disabled: {
    title: '记忆已关闭',
    description: '开启之后，我才会重新从新对话里记东西。',
    actionLabel: '重新开启',
  },
  unavailable: {
    title: '关于你的信息暂时没能取回来',
    description: '这不会影响你继续对话；稍后重新打开这一页就会再试一次。',
  },
  failed: {
    title: '记忆加载失败，请重试',
    description: '如果多次重试仍不成功，可联系团队管理员查看服务状态。',
  },
});

/** 顶部横幅（关闭态） */
export const MEMORY_DISABLED_BANNER =
  '记忆已关闭：我不会再从新对话里记东西。已记住的仍在这里，你可以删除。';

/** 页头说明 */
export const MEMORY_PAGE_DESCRIPTION =
  '这是我了解到的关于你的信息，你可以逐条查看和删除；删除后我不会再用它回答你。';

/** 单条删除确认（PRD §4.4 文案，逐字） */
export const MEMORY_DELETE_CONFIRM = {
  title: '删除这条信息？',
  description: '删除后，我不会再用这条信息回答你。对话记录不受影响。',
  confirmLabel: '删除',
} as const;

/** 按类删除确认（PRD §4.4：「关于[称呼]的信息将不再被使用」） */
export function memoryCategoryDeleteConfirm(categoryLabel: string): {
  title: string;
  description: string;
  confirmLabel: string;
} {
  return {
    title: `删除「${categoryLabel}」的全部信息？`,
    description: `删除后，关于${categoryLabel}的信息将不再被使用。对话记录不受影响。`,
    confirmLabel: '删除',
  };
}

/** 全删确认（PRD §4.4：需输入"删除"二次确认） */
export const MEMORY_DELETE_ALL_CONFIRM = {
  title: '删除我的全部记忆？',
  description: '我会忘掉关于你的一切，此操作不可撤销。对话记录不会受影响。',
  confirmLabel: '删除全部',
  keyword: '删除',
} as const;

/** 分类开关关闭后的说明（"已有条目保留"是合同条款，必须写出来） */
export const MEMORY_CATEGORY_SWITCH_HINT =
  '关闭后这一类不再被记录、也不再被使用；已经记住的仍在这里，你可以删除。';
