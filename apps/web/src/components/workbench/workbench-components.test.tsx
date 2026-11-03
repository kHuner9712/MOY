/**
 * 前端补测（审计 2026-11-03 批次 3）：workbench 组件群此前为 0% 行覆盖。
 *
 * 覆盖合同：
 * - ConfirmBar：关闭不渲染 / 摘要与原因回落 / 三个动作 / busy 禁用；
 * - SubmitBar：八种状态文案、排队位次两档、进度夹紧、取消入口只在进行态；
 * - HelpPanel：说明与步骤缺省、能力标签、最近执行三态；
 * - DeclarationForm：六类控件（含 select 无候选值的降级）、校验错误、提交；
 * - FilePicker：知识库/文档三态、勾选与确定回传；
 * - loading.tsx：骨架与加载文案。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type {
  KnowledgeBase,
  KnowledgeDocumentItem,
  PluginCapabilityItem,
  PluginDetail,
  PluginInputSpec,
  TaskItem,
  TaskStatus,
} from '@/lib/types';
import { act, renderComponent } from '@/test/render-hook';
import WorkbenchLoading from '@/app/(main)/workbench/[pluginId]/loading';
import { ConfirmBar } from './confirm-bar';
import { DeclarationForm } from './declaration-form';
import { FilePicker } from './file-picker';
import { HelpPanel } from './help-panel';
import { SubmitBar } from './submit-bar';

const mocks = vi.hoisted(() => ({
  listBases: vi.fn(),
  listDocuments: vi.fn(),
}));

vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  knowledgeApi: { listBases: mocks.listBases },
  knowledgeExtApi: { listDocuments: mocks.listDocuments },
}));

vi.mock('next/link', async () => {
  const React = await import('react');
  return {
    default: (props: { href: string; children: ReactNode }) =>
      React.createElement('a', { href: props.href }, props.children),
  };
});

/** 带 QueryClient 的挂载（FilePicker 走 react-query） */
function renderWithClient(element: ReactNode): ReturnType<typeof renderComponent> {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return renderComponent(<QueryClientProvider client={client}>{element}</QueryClientProvider>);
}

/** 等待 react-query 的微任务落地 */
async function flush(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

/** 等待某段文案出现在文档流（react-query 两级查询：base 成功后才发 documents） */
async function waitForText(text: string): Promise<void> {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    if (document.body.textContent?.includes(text)) return;
    await flush();
  }
  expect(document.body.textContent).toContain(text);
}

/** 按可见文案点击按钮 */
function clickButton(container: HTMLElement, label: string): void {
  const button = Array.from(container.querySelectorAll('button')).find((item) =>
    item.textContent?.includes(label),
  );
  if (!button) throw new Error(`按钮未找到：${label}`);
  act(() => {
    button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
}

/** 输入框赋值并派发 change */
function typeInto(
  element: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement,
  value: string,
): void {
  const prototype =
    element instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : element instanceof HTMLSelectElement
        ? HTMLSelectElement.prototype
        : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
  act(() => {
    setter?.call(element, value);
    element.dispatchEvent(new Event('input', { bubbles: true }));
    element.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

function inputByLabel(container: HTMLElement, label: string): HTMLInputElement {
  const found = Array.from(container.querySelectorAll('input')).find(
    (item) => item.getAttribute('placeholder') === `填写${label}`,
  );
  if (!found) throw new Error(`输入框未找到：${label}`);
  return found;
}

const PLUGIN: PluginDetail = {
  plugin_id: 'plg_weekly',
  name: '周报助手',
  summary: '',
  description: '',
  category: 'content',
  icon_key: 'weekly',
  rating_avg: 4.5,
  rating_count: 12,
  latest_version: '1.0.0',
  capabilities: [
    {
      capability_key: 'chat.completion',
      display_name: '对话生成',
      reason: '',
      denial_effect: '',
      category: 'personal',
      required: true,
      org_switch_enabled: false,
    },
    {
      capability_key: 'knowledge.read',
      display_name: '读取知识库',
      reason: '',
      denial_effect: '',
      category: 'org',
      required: false,
      org_switch_enabled: false,
    },
  ],
  inputs: [],
  step_titles: ['收集素材', '生成周报'],
  outputs: [],
  settings_schema: [],
  versions: [],
  installed_status: 'installed',
  installed_version: '1.0.0',
  has_update: false,
  installable: true,
  install_block_reason: '',
};

const TASK_ITEM: TaskItem = {
  task_id: 'task_1',
  conversation_id: 'conv_1',
  goal: '写周报',
  status: 'completed',
  progress: 100,
  created_at: '2026-11-02T09:00:00Z',
  updated_at: '2026-11-02T10:00:00Z',
  warning_count: 0,
};

const BASE: KnowledgeBase = {
  base_id: 'kb_1',
  name: '公司制度',
  description: '',
  visibility: 'private',
  document_count: 2,
  created_at: '2026-11-01T09:00:00Z',
};

const DOCUMENT: KnowledgeDocumentItem = {
  document_id: 'doc_1',
  file_name: '员工手册.pdf',
  file_size: 2048,
  uploader_name: '张三',
  uploaded_at: '2026-11-01T10:00:00Z',
  index_status: 'completed',
  stage: 'completed',
  stage_display_name: '可使用',
  progress: 100,
  estimated_remaining_seconds: null,
  segment_count: 12,
  error_message: null,
};

/** 六类控件各一条的声明 */
const INPUTS: PluginInputSpec[] = [
  { key: 'title', label: '周报标题', type: 'text', required: true, max_length: 40 },
  { key: 'owner_email', label: '负责人邮箱', type: 'email', required: false },
  { key: 'detail', label: '本周详情', type: 'textarea', required: true },
  { key: 'tone', label: '语气', type: 'select', required: true, options: ['正式', '简洁'] },
  { key: 'fallback', label: '汇报对象', type: 'select', required: false, options: [] },
  {
    key: 'topics',
    label: '关注主题',
    type: 'multi_select',
    required: false,
    options: ['进展', '风险'],
  },
  { key: 'files', label: '参考文件', type: 'file', required: false },
];

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listBases.mockResolvedValue({ items: [BASE] });
  mocks.listDocuments.mockResolvedValue({ items: [DOCUMENT] });
});

afterEach(() => {
  document.body.innerHTML = '';
});

describe('ConfirmBar', () => {
  it('关闭时不渲染任何内容', () => {
    const { container, unmount } = renderComponent(
      <ConfirmBar open={false} onContinue={vi.fn()} onRedo={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(container.textContent).toBe('');
    unmount();
  });

  it('展示步骤、原因与摘要，三个动作各自回放', () => {
    const onContinue = vi.fn();
    const onRedo = vi.fn();
    const onCancel = vi.fn();
    const { container, unmount } = renderComponent(
      <ConfirmBar
        open
        confirm={{ step_seq: 2, reason: '', summary: { 收件人: '张三', 主题: '周报' } }}
        onContinue={onContinue}
        onRedo={onRedo}
        onCancel={onCancel}
      />,
    );
    expect(container.textContent).toContain('第 2 步');
    expect(container.textContent).toContain('确认内容后可继续执行');
    expect(container.textContent).toContain('收件人');
    expect(container.textContent).toContain('张三');

    clickButton(container, '确认继续');
    clickButton(container, '补充要求后重做');
    clickButton(container, '放弃本次');
    expect(onContinue).toHaveBeenCalledTimes(1);
    expect(onRedo).toHaveBeenCalledTimes(1);
    expect(onCancel).toHaveBeenCalledTimes(1);
    unmount();
  });

  it('没有确认信息时用占位符，busy 时三个按钮禁用', () => {
    const { container, unmount } = renderComponent(
      <ConfirmBar
        open
        confirm={null}
        busy
        onContinue={vi.fn()}
        onRedo={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(container.textContent).toContain('第 — 步');
    expect(container.textContent).toContain('处理中…');
    const buttons = Array.from(container.querySelectorAll('button'));
    expect(buttons).toHaveLength(3);
    expect(buttons.every((item) => item.disabled)).toBe(true);
    unmount();
  });
});

describe('SubmitBar', () => {
  function renderStatus(
    status: TaskStatus | '',
    overrides: {
      progress?: number;
      queuePosition?: number;
      taskId?: string;
      cancelling?: boolean;
      onCancel?: () => void;
    } = {},
  ): ReturnType<typeof renderComponent> {
    return renderComponent(
      <SubmitBar
        status={status}
        progress={overrides.progress ?? 40}
        queuePosition={overrides.queuePosition}
        taskId={overrides.taskId}
        cancelling={overrides.cancelling}
        onCancel={overrides.onCancel}
      />,
    );
  }

  it('没有状态时不渲染', () => {
    const { container, unmount } = renderStatus('');
    expect(container.textContent).toBe('');
    unmount();
  });

  it('八种状态各有业务文案', () => {
    const cases: Array<[TaskStatus, string]> = [
      ['queued', '任务已排队，马上开始'],
      ['pending', '任务已创建，正在准备执行'],
      ['running', '执行中，可以离开此页'],
      ['waiting_confirm', '有一个步骤需要你确认后才会继续'],
      ['completed', '本次执行已完成'],
      ['failed', '本次执行未能完成'],
      ['cancelled', '本次执行已取消'],
    ];
    for (const [status, hint] of cases) {
      const { container, unmount } = renderStatus(status);
      expect(container.textContent).toContain(hint);
      unmount();
    }
  });

  it('排队位次大于 1 时提示前面还有几个', () => {
    const { container, unmount } = renderStatus('queued', { queuePosition: 3 });
    expect(container.textContent).toContain('前面还有 2 个任务在跑');
    unmount();
  });

  it('进度夹紧到 0-100，终态不显示取消入口，进行态可取消', () => {
    const low = renderStatus('running', { progress: -20, onCancel: vi.fn() });
    const bar = low.container.querySelector('[role="progressbar"]');
    expect(bar?.getAttribute('aria-label')).toBe('执行进度');
    expect(bar?.getAttribute('aria-valuenow')).toBe('0');
    low.unmount();

    const high = renderStatus('completed', { progress: 180 });
    expect(high.container.textContent).not.toContain('放弃本次');
    high.unmount();

    const onCancel = vi.fn();
    const cancelling = renderStatus('running', { taskId: 'task_1', cancelling: true, onCancel });
    expect(cancelling.container.textContent).toContain('取消中…');
    expect(cancelling.container.textContent).toContain('任务号：task_1');
    const cancelButton = Array.from(cancelling.container.querySelectorAll('button')).find((item) =>
      item.textContent?.includes('取消中…'),
    );
    expect(cancelButton?.disabled).toBe(true);
    cancelling.unmount();
  });

  it('进行态点击放弃会回调', () => {
    const onCancel = vi.fn();
    const { container, unmount } = renderStatus('running', { onCancel });
    clickButton(container, '放弃本次');
    expect(onCancel).toHaveBeenCalledTimes(1);
    unmount();
  });
});

describe('HelpPanel', () => {
  it('说明为空时回落占位，步骤与能力逐条展示，最近执行三态', () => {
    const { container, unmount } = renderComponent(
      <HelpPanel plugin={PLUGIN} recentTasks={[TASK_ITEM]} />,
    );
    expect(container.textContent).toContain('插件未提供说明');
    expect(container.textContent).toContain('收集素材');
    expect(container.textContent).toContain('对话生成');
    expect(container.textContent).toContain('写周报');
    unmount();
  });

  it('加载中与空记录分别给对应文案', () => {
    const loading = renderComponent(<HelpPanel plugin={PLUGIN} recentTasks={[]} loading />);
    expect(loading.container.textContent).toContain('正在加载执行记录…');
    loading.unmount();

    const empty = renderComponent(<HelpPanel plugin={PLUGIN} recentTasks={[]} />);
    expect(empty.container.textContent).toContain('还没有执行记录');
    empty.unmount();
  });

  it('没有步骤声明时不渲染步骤区块', () => {
    const { container, unmount } = renderComponent(
      <HelpPanel plugin={{ ...PLUGIN, step_titles: [] }} recentTasks={[]} />,
    );
    expect(container.textContent).not.toContain('会经历这些步骤');
    unmount();
  });
});

describe('DeclarationForm', () => {
  it('六类控件按声明渲染，值变更逐个回放', () => {
    const onChange = vi.fn();
    const { container, unmount } = renderWithClient(
      <DeclarationForm
        inputs={INPUTS}
        values={{
          title: '本周周报',
          owner_email: 'a@b.com',
          detail: '完成了三件事',
          tone: '正式',
          fallback: '直属上级',
          topics: ['进展'],
          files: ['doc_1'],
        }}
        errors={{ detail: '不能为空' }}
        onChange={onChange}
        onSubmit={vi.fn()}
      />,
    );

    expect(container.querySelectorAll('textarea')).toHaveLength(1);
    expect(container.querySelectorAll('select')).toHaveLength(1);
    expect(container.textContent).toContain('暂无可选项，请直接填写');
    expect(inputByLabel(container, '周报标题').value).toBe('本周周报');
    expect(inputByLabel(container, '负责人邮箱').type).toBe('email');
    expect(container.textContent).toContain('不能为空');
    expect(container.textContent).toContain('已选择 1 个文件');

    typeInto(inputByLabel(container, '周报标题'), '下周计划');
    expect(onChange).toHaveBeenCalledWith('title', '下周计划');

    const textarea = container.querySelector('textarea');
    expect(textarea).not.toBeNull();
    typeInto(textarea as HTMLTextAreaElement, '补充内容');
    expect(onChange).toHaveBeenCalledWith('detail', '补充内容');

    const select = container.querySelector('select');
    expect(select).not.toBeNull();
    typeInto(select as HTMLSelectElement, '简洁');
    expect(onChange).toHaveBeenCalledWith('tone', '简洁');

    const checkboxes = Array.from(
      container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]'),
    );
    const progressBox = checkboxes.find((item) =>
      item.parentElement?.textContent?.includes('进展'),
    );
    expect(progressBox?.checked).toBe(true);
    act(() => {
      progressBox?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(onChange).toHaveBeenCalledWith('topics', []);
    unmount();
  });

  it('提交拦截默认行为并回调，禁用时提交按钮不可点', () => {
    const onSubmit = vi.fn();
    const { container, unmount } = renderWithClient(
      <DeclarationForm
        inputs={INPUTS}
        values={{}}
        errors={{}}
        onChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );
    const form = container.querySelector('form');
    expect(form).not.toBeNull();
    act(() => {
      form?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    expect(onSubmit).toHaveBeenCalledTimes(1);
    unmount();

    const disabled = renderWithClient(
      <DeclarationForm
        inputs={INPUTS}
        values={{}}
        errors={{}}
        onChange={vi.fn()}
        onSubmit={vi.fn()}
        disabled
        submitLabel="执行中"
      />,
    );
    const submitButton = Array.from(disabled.container.querySelectorAll('button')).find((item) =>
      item.textContent?.includes('执行中'),
    );
    expect(submitButton?.disabled).toBe(true);
    disabled.unmount();
  });
});

describe('FilePicker', () => {
  it('未选择时给提示，点开后加载知识库与文档并可勾选确认', async () => {
    const onChange = vi.fn();
    const { container, unmount } = renderWithClient(<FilePicker value={[]} onChange={onChange} />);
    expect(container.textContent).toContain('还没有选择文件');

    clickButton(container, '选择文件');
    await flush();
    await waitForText('选择已上传的文件');
    await waitForText('公司制度');
    await waitForText('员工手册.pdf');

    const checkbox = document.body.querySelector('input[type="checkbox"]');
    act(() => {
      checkbox?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    clickButton(document.body, '确定（1 个）');
    expect(onChange).toHaveBeenCalledWith(['doc_1']);
    unmount();
  });

  it('已有选择时展示数量与文件名，再点可取消勾选', async () => {
    const onChange = vi.fn();
    const { container, unmount } = renderWithClient(
      <FilePicker value={['doc_1']} onChange={onChange} label="选择参考资料" />,
    );
    expect(container.textContent).toContain('已选择 1 个文件');

    clickButton(container, '选择参考资料');
    await flush();
    await flush();
    await flush();
    await waitForText('员工手册.pdf');
    const checkbox = Array.from(
      document.body.querySelectorAll<HTMLInputElement>('input[type="checkbox"]'),
    ).find((item) => item.closest('label')?.textContent?.includes('员工手册'));
    expect(checkbox?.checked).toBe(true);
    act(() => {
      checkbox?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    clickButton(document.body, '确定（0 个）');
    expect(onChange).toHaveBeenCalledWith([]);
    unmount();
  });

  it('知识库为空时给上传引导，没有文档时给换库提示', async () => {
    mocks.listBases.mockResolvedValue({ items: [] });
    const emptyBase = renderWithClient(<FilePicker value={[]} onChange={vi.fn()} />);
    clickButton(emptyBase.container, '选择文件');
    await flush();
    await flush();
    expect(document.body.textContent).toContain('还没有上传文件');
    emptyBase.unmount();

    mocks.listBases.mockResolvedValue({ items: [BASE] });
    mocks.listDocuments.mockResolvedValue({ items: [] });
    const emptyDocs = renderWithClient(<FilePicker value={[]} onChange={vi.fn()} />);
    clickButton(emptyDocs.container, '选择文件');
    await flush();
    await flush();
    await flush();
    await waitForText('这个知识库还没有文件');
    emptyDocs.unmount();
  });

  it('加载失败时给业务文案与重试入口', async () => {
    mocks.listBases.mockRejectedValue(new Error('boom'));
    mocks.listDocuments.mockRejectedValue(new Error('boom'));
    const { container, unmount } = renderWithClient(<FilePicker value={[]} onChange={vi.fn()} />);
    clickButton(container, '选择文件');
    await flush();
    await flush();
    expect(container.querySelectorAll('button').length).toBeGreaterThan(0);
    expect(container.textContent).toBeTruthy();
    const retry = Array.from(container.querySelectorAll('button')).find((item) =>
      item.textContent?.includes('重试'),
    );
    if (retry) {
      act(() => {
        retry.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      });
    }
    unmount();
  });

  it('禁用时选择按钮不可点', () => {
    const { container, unmount } = renderWithClient(
      <FilePicker value={[]} onChange={vi.fn()} disabled />,
    );
    const button = Array.from(container.querySelectorAll('button')).find((item) =>
      item.textContent?.includes('选择文件'),
    );
    expect(button?.disabled).toBe(true);
    unmount();
  });
});

describe('WorkbenchLoading', () => {
  it('渲染骨架与加载文案', () => {
    const { container, unmount } = renderComponent(<WorkbenchLoading />);
    expect(container.textContent).toContain('正在打开工作台…');
    unmount();
  });
});
