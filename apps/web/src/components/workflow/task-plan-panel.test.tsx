/**
 * 矩阵第 2 项（计划生成 → 确认）前端交互测试。
 *
 * 覆盖：计划展示（必做/可取消/工具与产物标签）、勾选改动必须先保存再确认、
 * 确认前的执行闸门（有未保存调整时确认按钮禁用）、计划过期后整体禁用只留「放弃本次」。
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import type { TaskPlan, TaskPlanStep } from '@/lib/types';
import { act, renderComponent } from '@/test/render-hook';

import { TaskPlanPanel } from './task-plan-panel';

afterEach(() => {
  document.body.innerHTML = '';
});

function makeStep(overrides: Partial<TaskPlanStep> & { seq: number }): TaskPlanStep {
  return {
    title: `步骤 ${overrides.seq}`,
    action: 'compose',
    tool_type: null,
    tool_operation: null,
    expected_artifacts: [],
    needs: [],
    skippable: true,
    selected: true,
    note: '',
    ...overrides,
  };
}

const PLAN: TaskPlan = {
  task_id: 'task_1',
  plugin_id: 'plg_demo',
  version: 1,
  expires_at: '2026-11-06T10:15:00Z',
  confirmed_at: null,
  steps: [
    makeStep({
      seq: 1,
      title: '整理要点',
      action: 'compose',
      expected_artifacts: ['draft'],
      skippable: false,
    }),
    makeStep({
      seq: 2,
      title: '同步日历',
      action: 'tool',
      tool_type: 'feishu',
      tool_operation: 'create_calendar_event',
      needs: ['draft'],
      note: '执行前需要你确认',
    }),
  ],
};

/** 按可见文案找按钮 */
function findButton(root: ParentNode, label: string): HTMLButtonElement | undefined {
  return Array.from(root.querySelectorAll('button')).find((item) =>
    item.textContent?.includes(label),
  );
}

/** 按步骤序号找勾选框 */
function findCheckbox(root: ParentNode, seq: number): HTMLInputElement {
  const input = root.querySelector<HTMLInputElement>(`#plan-step-${seq}`);
  if (!input) throw new Error(`勾选框未找到：${seq}`);
  return input;
}

function click(element: Element | null | undefined): void {
  act(() => {
    element?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
}

describe('TaskPlanPanel', () => {
  it('展示步骤、必做标记、工具与预计产物', () => {
    const { container, unmount } = renderComponent(
      <TaskPlanPanel
        plan={PLAN}
        now={Date.parse('2026-11-06T10:00:00Z')}
        onUpdate={vi.fn()}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(container.textContent).toContain('执行计划已生成，共 2 步');
    expect(container.textContent).toContain('整理要点');
    expect(container.textContent).toContain('必做步骤');
    expect(container.textContent).toContain('feishu · create_calendar_event');
    expect(container.textContent).toContain('预计产物：draft');
    expect(container.textContent).toContain('执行前需要你确认');
    expect(container.textContent).toContain('请在 15 分 0 秒内确认');

    // 必做步骤不可取消勾选
    expect(findCheckbox(container, 1).disabled).toBe(true);
    expect(findCheckbox(container, 2).disabled).toBe(false);
    unmount();
  });

  it('勾选改动必须先保存；确认在未保存时被禁用', () => {
    const onUpdate = vi.fn();
    const onConfirm = vi.fn();
    const { container, unmount } = renderComponent(
      <TaskPlanPanel
        plan={PLAN}
        now={Date.parse('2026-11-06T10:00:00Z')}
        onUpdate={onUpdate}
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />,
    );

    // 初始无改动：确认可用、保存禁用
    expect(findButton(container, '确认并执行')?.disabled).toBe(false);
    expect(findButton(container, '保存调整')?.disabled).toBe(true);

    click(findCheckbox(container, 2));

    expect(container.textContent).toContain('有未保存的调整，请先保存再确认');
    expect(findButton(container, '保存调整')?.disabled).toBe(false);
    expect(findButton(container, '确认并执行')?.disabled).toBe(true);

    // 直接点确认不会提交任何东西（disabled）
    click(findButton(container, '确认并执行'));
    expect(onConfirm).not.toHaveBeenCalled();

    click(findButton(container, '保存调整'));
    expect(onUpdate).toHaveBeenCalledWith([
      { seq: 1, selected: true },
      { seq: 2, selected: false },
    ]);

    // 保存后（新计划到达前）草稿与服务端仍有差异，确认保持禁用——避免执行服务端旧勾选
    expect(findButton(container, '确认并执行')?.disabled).toBe(true);
    unmount();
  });

  it('无改动时确认直接提交', () => {
    const onConfirm = vi.fn();
    const { container, unmount } = renderComponent(
      <TaskPlanPanel
        plan={PLAN}
        now={Date.parse('2026-11-06T10:00:00Z')}
        onUpdate={vi.fn()}
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />,
    );

    click(findButton(container, '确认并执行'));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    unmount();
  });

  it('放弃本次始终可用，走后端取消', () => {
    const onCancel = vi.fn();
    const { container, unmount } = renderComponent(
      <TaskPlanPanel
        plan={PLAN}
        now={Date.parse('2026-11-06T10:00:00Z')}
        onUpdate={vi.fn()}
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />,
    );

    click(findButton(container, '放弃本次'));
    expect(onCancel).toHaveBeenCalledTimes(1);
    unmount();
  });

  it('计划过期后禁用编辑与确认，只保留放弃', () => {
    const onConfirm = vi.fn();
    const onUpdate = vi.fn();
    const { container, unmount } = renderComponent(
      <TaskPlanPanel
        plan={PLAN}
        now={Date.parse('2026-11-06T10:20:00Z')}
        onUpdate={onUpdate}
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />,
    );

    expect(container.textContent).toContain('计划已过期，不会再执行');
    expect(container.textContent).toContain('计划在确认前已超时');
    expect(container.textContent).toContain('已过期');
    expect(findCheckbox(container, 2).disabled).toBe(true);
    expect(findButton(container, '保存调整')?.disabled).toBe(true);
    expect(findButton(container, '确认并执行')?.disabled).toBe(true);
    expect(findButton(container, '放弃本次')?.disabled).toBe(false);

    click(findButton(container, '确认并执行'));
    expect(onConfirm).not.toHaveBeenCalled();
    unmount();
  });
});
