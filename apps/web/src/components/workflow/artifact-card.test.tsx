/**
 * ArtifactCard 的入口渲染合同（2026-11-05 B2）。
 *
 * 覆盖三件事：
 * 1. `xlsx_url` / `preview_url` 是**后端决定**的入口：有才渲染，没有就整颗按钮不出现；
 * 2. 预览是 PDF 链接（新窗口），不是内嵌 iframe；
 * 3. WF2 编辑格式下拉包含 `xlsx`（表格重新导出 Excel）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { TaskArtifact } from '@/lib/types';
import { act, renderComponent } from '@/test/render-hook';
import { ArtifactCard } from './artifact-card';

const mocks = vi.hoisted(() => ({
  mutate: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('@/components/ui/toast', () => ({ toast: mocks.toast }));

vi.mock('@/hooks/use-api', () => ({
  QUERY_KEYS: {
    pluginTask: (taskId: string) => ['pluginTask', taskId],
    pluginTaskSteps: (taskId: string) => ['pluginTaskSteps', taskId],
  },
  useApiMutation: () => ({ mutate: mocks.mutate, isPending: false }),
}));

vi.mock('@/lib/api-client', () => ({
  agentApi: { updateArtifact: vi.fn() },
}));

/** 基础表格产物（带两个 B2 新增入口） */
const TABLE_ARTIFACT: TaskArtifact = {
  artifact_id: 'art_schedule',
  type: 'table',
  name: '发布排期表',
  preview_text: '平台,内容\n小红书,种草',
  inline_text: '平台,内容\n小红书,种草',
  xlsx_url: '/files/artifacts/token-1/export?format=xlsx',
  preview_url: '/files/artifacts/token-1/preview',
};

/** 按可见文案查找按钮/链接 */
function findByText(container: HTMLElement, text: string): HTMLElement | undefined {
  return Array.from(container.querySelectorAll<HTMLElement>('a, button')).find((item) =>
    item.textContent?.includes(text),
  );
}

/** 按可见文案点击 */
function clickByText(container: HTMLElement, text: string): void {
  const element = findByText(container, text);
  if (!element) {
    throw new Error(`入口未找到：${text}`);
  }
  act(() => {
    element.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
}

let mounted: { container: HTMLElement; unmount: () => void } | null = null;

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  mounted?.unmount();
  mounted = null;
  vi.unstubAllGlobals();
});

describe('ArtifactCard 的 Excel / 预览入口', () => {
  it('表格产物有 xlsx_url 与 preview_url 时渲染两个入口且指向后端地址', () => {
    mounted = renderComponent(<ArtifactCard taskId="task-1" artifact={TABLE_ARTIFACT} />);
    const xlsx = findByText(mounted.container, '下载 Excel');
    const preview = findByText(mounted.container, '预览');
    expect(xlsx?.tagName).toBe('A');
    expect(xlsx?.getAttribute('href')).toBe('/files/artifacts/token-1/export?format=xlsx');
    expect(xlsx?.getAttribute('target')).toBe('_blank');
    expect(preview?.tagName).toBe('A');
    expect(preview?.getAttribute('href')).toBe('/files/artifacts/token-1/preview');
    expect(preview?.getAttribute('target')).toBe('_blank');
    // 预览不能是 iframe：PDF 要交给浏览器自带阅读器
    expect(mounted.container.querySelector('iframe')).toBeNull();
  });

  it('没有 xlsx_url / preview_url 时两个入口都不出现（前端不做二次判断）', () => {
    mounted = renderComponent(
      <ArtifactCard
        taskId="task-1"
        artifact={{ ...TABLE_ARTIFACT, xlsx_url: null, preview_url: null }}
      />,
    );
    expect(findByText(mounted.container, '下载 Excel')).toBeUndefined();
    expect(findByText(mounted.container, '预览')).toBeUndefined();
  });

  it('文件产物仍保留下载入口，并可与预览并列', () => {
    mounted = renderComponent(
      <ArtifactCard
        taskId="task-1"
        artifact={{
          artifact_id: 'art_docx',
          type: 'file',
          name: '行政公文',
          download_url: '/files/artifacts/token-2',
          preview_url: '/files/artifacts/token-2/preview',
          size_bytes: 2048,
          content_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        }}
      />,
    );
    expect(findByText(mounted.container, '下载')?.getAttribute('href')).toBe(
      '/files/artifacts/token-2',
    );
    expect(findByText(mounted.container, '预览')?.getAttribute('href')).toBe(
      '/files/artifacts/token-2/preview',
    );
    // 只读类型不出现编辑入口
    expect(findByText(mounted.container, '编辑并导出')).toBeUndefined();
  });

  it('编辑弹窗的格式下拉包含 Excel 表格（WF2 可导出 xlsx）', () => {
    mounted = renderComponent(<ArtifactCard taskId="task-1" artifact={TABLE_ARTIFACT} />);
    clickByText(mounted.container, '编辑并导出');
    // Dialog 走 Portal 渲染到 document.body，不在挂载容器内
    const options = Array.from(document.body.querySelectorAll('option')).map(
      (item) => item.textContent,
    );
    expect(options).toContain('Excel 表格');
    expect(options).toContain('Word 文档');
  });

  it('复制表格成功/失败各自给出业务提示', async () => {
    mounted = renderComponent(<ArtifactCard taskId="task-1" artifact={TABLE_ARTIFACT} />);
    vi.stubGlobal('navigator', {
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
    clickByText(mounted.container, '复制表格');
    await Promise.resolve();
    await Promise.resolve();
    expect(mocks.toast.success).toHaveBeenCalledWith('已复制表格内容，可粘贴到表格软件');

    vi.stubGlobal('navigator', {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    });
    clickByText(mounted.container, '复制表格');
    await Promise.resolve();
    await Promise.resolve();
    expect(mocks.toast.error).toHaveBeenCalled();
  });
});
