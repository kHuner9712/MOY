/**
 * 「我的记忆」文案回归网（PRD §4.4 / §6.1 合同条款）。
 *
 * 断言重点：6 套空态齐全、每套都有标题与说明、确认文案逐字插值，
 * 以及**不得出现技术黑盒词**（禁词口径见文件头）。
 */

import { describe, expect, it } from 'vitest';

import {
  MEMORY_CATEGORY_LABELS,
  MEMORY_DELETE_ALL_CONFIRM,
  MEMORY_DELETE_CONFIRM,
  MEMORY_DISABLED_BANNER,
  MEMORY_EMPTY_COPY,
  MEMORY_PAGE_DESCRIPTION,
  MEMORY_CATEGORY_SWITCH_HINT,
  memoryCategoryDeleteConfirm,
} from './memory-labels';
import type { MemoryEmptyKind } from './memory-labels';

describe('记忆类别与空态', () => {
  it('类别标签覆盖 6 类且均为业务语言', () => {
    expect(Object.keys(MEMORY_CATEGORY_LABELS)).toEqual([
      'identity',
      'org',
      'preference',
      'convention',
      'constraint',
      'project',
    ]);
    expect(MEMORY_CATEGORY_LABELS.identity).toBe('称呼');
  });

  it('6 套空态齐全，每套都有标题与说明', () => {
    const kinds: MemoryEmptyKind[] = [
      'never',
      'cleared',
      'filtered',
      'disabled',
      'unavailable',
      'failed',
    ];
    for (const kind of kinds) {
      const copy = MEMORY_EMPTY_COPY[kind];
      expect(copy.title.length).toBeGreaterThan(0);
      expect(copy.description.length).toBeGreaterThan(0);
    }
    expect(MEMORY_EMPTY_COPY.never.actionLabel).toBe('去看对话');
    expect(MEMORY_EMPTY_COPY.cleared.actionLabel).toBeUndefined();
  });

  it('空态与说明文案不含技术黑盒词', () => {
    const texts = [
      ...Object.values(MEMORY_EMPTY_COPY).flatMap((copy) => [copy.title, copy.description]),
      MEMORY_DISABLED_BANNER,
      MEMORY_PAGE_DESCRIPTION,
      MEMORY_CATEGORY_SWITCH_HINT,
    ].join(' ');
    for (const banned of [
      '向量',
      'embedding',
      '召回',
      'top-k',
      '相似度',
      '阈值',
      '租户',
      '错误码',
    ]) {
      expect(texts).not.toContain(banned);
    }
  });
});

describe('删除确认文案', () => {
  it('单条删除确认与 PRD 逐字一致', () => {
    expect(MEMORY_DELETE_CONFIRM.title).toBe('删除这条信息？');
    expect(MEMORY_DELETE_CONFIRM.confirmLabel).toBe('删除');
  });

  it('按类删除会把类别名插值进标题与说明', () => {
    const confirm = memoryCategoryDeleteConfirm('称呼');
    expect(confirm.title).toBe('删除「称呼」的全部信息？');
    expect(confirm.description).toBe('删除后，关于称呼的信息将不再被使用。对话记录不受影响。');
    expect(confirm.confirmLabel).toBe('删除');
  });

  it('全删需要输入「删除」二次确认', () => {
    expect(MEMORY_DELETE_ALL_CONFIRM.keyword).toBe('删除');
    expect(MEMORY_DELETE_ALL_CONFIRM.confirmLabel).toBe('删除全部');
  });
});
