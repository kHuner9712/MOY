/**
 * 分页组件单测（Batch 2 矩阵 #15/#16）。
 *
 * 只测纯函数，不挂渲染器：本仓库 monorepo 根目录另有一份 React 18，
 * 把渲染器拉进 vitest 会把两套 React 混在一起（见 `vitest.config.mts` 注释）。
 * 分页的「修正」逻辑恰好被拆成了两个纯函数，因此不需要渲染也能钉住行为。
 */

import { describe, expect, it } from 'vitest';

import { clampPage, resolvePageState } from './pagination';

describe('clampPage', () => {
  it('总页数只有 1 页时，任何页码都收敛到 1', () => {
    expect(clampPage(3, 1)).toBe(1);
  });

  it('页码小于 1 时收敛到 1', () => {
    expect(clampPage(0, 3)).toBe(1);
    expect(clampPage(-7, 3)).toBe(1);
  });

  it('页码超过总页数时收敛到最后一页', () => {
    expect(clampPage(5, 3)).toBe(3);
  });

  it('总页数为 0 或非法时按 1 页算', () => {
    expect(clampPage(1, 0)).toBe(1);
    expect(clampPage(1, Number.NaN)).toBe(1);
  });
});

describe('resolvePageState（total 收缩时推出应落的页码）', () => {
  it('数据从 30 条降到 10 条：总页数 3 → 1，当前页被推回 1', () => {
    // 修复前的形态：分页条显示「第 1 / 1 页」，但父级 page 仍是 3，
    // `useApiQuery` 的 key 带着 page=3 继续请求，列表永远空白。
    const shrunk = resolvePageState(3, 10, 10);

    expect(shrunk.totalPages).toBe(1);
    expect(shrunk.currentPage).toBe(1);
  });

  it('未越界时原样保留当前页（不得无故把用户弹回第 1 页）', () => {
    const steady = resolvePageState(2, 10, 30);

    expect(steady.totalPages).toBe(3);
    expect(steady.currentPage).toBe(2);
  });

  it('空数据集：总页数按 1 算，当前页为 1', () => {
    const empty = resolvePageState(4, 20, 0);

    expect(empty.safeTotal).toBe(0);
    expect(empty.totalPages).toBe(1);
    expect(empty.currentPage).toBe(1);
  });

  it('每页条数非法时按 10 兜底，不影响收敛结果', () => {
    const fallback = resolvePageState(2, 0, 25);

    expect(fallback.safePageSize).toBe(10);
    expect(fallback.totalPages).toBe(3);
    expect(fallback.currentPage).toBe(2);
  });
});
