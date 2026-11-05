'use client';

import {
  Copy,
  Download,
  ExternalLink,
  Eye,
  FileSpreadsheet,
  FileText,
  History,
  Pencil,
} from 'lucide-react';
import * as React from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Field, Textarea } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { toast } from '@/components/ui/toast';
import { QUERY_KEYS, useApiMutation } from '@/hooks/use-api';
import { agentApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { formatDateTime, formatFileSize, orPlaceholder } from '@/lib/format';
import { isEditableArtifactType, type ArtifactRenderFormat, type TaskArtifact } from '@/lib/types';

/** 产物类型展示名（5 类） */
const TYPE_LABELS: Record<string, string> = {
  draft: '稿件',
  doc: '文档',
  table: '表格',
  link: '链接',
  file: '文件',
  text: '文本',
};

/** 导出格式选项（WF2 `render_format`） */
const FORMAT_OPTIONS: Array<{ value: ArtifactRenderFormat; label: string }> = [
  { value: 'docx', label: 'Word 文档' },
  { value: 'xlsx', label: 'Excel 表格' },
  { value: 'md', label: 'Markdown' },
  { value: 'csv', label: 'CSV 表格' },
];

/** 本地版本记录（只读回看） */
interface LocalVersion {
  version: number;
  content: string;
  downloadUrl: string;
  savedAt: string;
}

/** 产物卡片属性 */
export interface ArtifactCardProps {
  /** 所属任务（WF2 路径需要） */
  taskId: string;
  /** 该步产物（A4） */
  artifact: TaskArtifact;
  /** 保存成功回调（父级可据此刷新 A4） */
  onUpdated?: () => void;
}

/**
 * 产物卡片（5 类渲染 + WF2 编辑）。
 *
 * 硬约束：
 * - **只读类型（`link` / `file` / `text`）不渲染编辑入口**——不是置灰，是整颗按钮都不出现；
 * - 保存成功后展示「版本 vN」，并把旧版本放进「历史版本（只读）」；
 * - 过期产物给出「产物已过期，可重新发起任务」，不展示任何码值。
 *
 * @param props - 组件属性
 * @returns 产物卡片元素
 */
export function ArtifactCard({
  taskId,
  artifact,
  onUpdated,
}: ArtifactCardProps): React.JSX.Element {
  const editable = isEditableArtifactType(artifact.type);
  const [version, setVersion] = React.useState<number>(1);
  const [downloadUrl, setDownloadUrl] = React.useState<string>(artifact.download_url ?? '');
  const [history, setHistory] = React.useState<LocalVersion[]>([]);
  const [editOpen, setEditOpen] = React.useState<boolean>(false);
  const [draft, setDraft] = React.useState<string>('');
  const [format, setFormat] = React.useState<ArtifactRenderFormat>('docx');

  const content = artifact.inline_text ?? '';
  // 过期判断放到 effect 里算（渲染期调用 Date.now() 属于副作用，会被 react-hooks/purity 拦截）
  const [expired, setExpired] = React.useState<boolean>(false);
  React.useEffect(() => {
    if (!artifact.expires_at) {
      setExpired(false);
      return;
    }
    setExpired(Date.parse(artifact.expires_at) < Date.now());
  }, [artifact.expires_at]);

  const editMutation = useApiMutation(
    (payload: { content: string; render_format: ArtifactRenderFormat }) =>
      agentApi.updateArtifact(taskId, artifact.artifact_id, payload),
    {
      invalidateKeys: [QUERY_KEYS.pluginTask(taskId), QUERY_KEYS.pluginTaskSteps(taskId)],
      onSuccess: (data) => {
        setHistory((prev) => [
          { version, content, downloadUrl, savedAt: new Date().toISOString() },
          ...prev,
        ]);
        setVersion(data.version);
        setDownloadUrl(data.download_url);
        setEditOpen(false);
        toast.success(`已保存并重新导出（版本 v${data.version}）`);
        onUpdated?.();
      },
      onError: (error) => toast.error(toBusinessMessage(error)),
    },
  );

  /** 复制表格内容（表格类产物的 CSV 操作） */
  const copyTable = async (): Promise<void> => {
    const text = artifact.preview_text ?? '';
    try {
      await navigator.clipboard.writeText(text);
      toast.success('已复制表格内容，可粘贴到表格软件');
    } catch {
      toast.error('复制失败，请手动选择内容复制');
    }
  };

  /** 打开编辑弹窗 */
  const openEditor = (): void => {
    setDraft(content);
    setEditOpen(true);
  };

  return (
    <div className="rounded-md border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <FileText aria-hidden="true" className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="truncate text-sm font-medium" title={artifact.name}>
            {orPlaceholder(artifact.name, '未命名产物')}
          </span>
          <Badge variant="outline">{TYPE_LABELS[artifact.type] ?? '文本'}</Badge>
          <Badge variant="secondary">版本 v{version}</Badge>
        </div>

        <div className="flex items-center gap-1">
          {artifact.type === 'table' ? (
            <Button type="button" size="sm" variant="ghost" onClick={() => void copyTable()}>
              <Copy className="h-3.5 w-3.5" />
              复制表格
            </Button>
          ) : null}

          {/* 按需导出 Excel：`xlsx_url` 由后端按类型+开关决定，前端不做二次判断 */}
          {artifact.xlsx_url ? (
            <Button asChild size="sm" variant="outline">
              <a href={artifact.xlsx_url} target="_blank" rel="noreferrer">
                <FileSpreadsheet className="h-3.5 w-3.5" />
                下载 Excel
              </a>
            </Button>
          ) : null}

          {/* 预览是 PDF（浏览器自带阅读器），新窗口打开而非内嵌 iframe */}
          {artifact.preview_url ? (
            <Button asChild size="sm" variant="outline">
              <a href={artifact.preview_url} target="_blank" rel="noreferrer">
                <Eye className="h-3.5 w-3.5" />
                预览
              </a>
            </Button>
          ) : null}

          {artifact.type === 'link' && artifact.external_url ? (
            <Button asChild size="sm" variant="outline">
              <a href={artifact.external_url} target="_blank" rel="noreferrer">
                <ExternalLink className="h-3.5 w-3.5" />
                打开链接
              </a>
            </Button>
          ) : null}

          {(artifact.type === 'file' || artifact.type === 'doc') && downloadUrl ? (
            <Button asChild size="sm" variant="outline">
              <a href={downloadUrl} target="_blank" rel="noreferrer">
                <Download className="h-3.5 w-3.5" />
                下载
              </a>
            </Button>
          ) : null}

          {/* 编辑入口只对 draft/doc/table 渲染；link/file/text 连按钮都不出现 */}
          {editable ? (
            <Button type="button" size="sm" variant="outline" onClick={openEditor}>
              <Pencil className="h-3.5 w-3.5" />
              编辑并导出
            </Button>
          ) : null}
        </div>
      </div>

      {expired ? (
        <p className="mt-2 rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground">
          产物已过期，可重新发起任务以获取新版本
        </p>
      ) : null}

      {artifact.type === 'file' ? (
        <p className="mt-2 text-xs text-muted-foreground">
          {formatFileSize(artifact.size_bytes ?? 0)}
          {!downloadUrl ? ' · 下载地址由平台生成，可稍后在任务详情重试' : ''}
        </p>
      ) : null}

      {content ? (
        <pre className="mt-2 max-h-60 overflow-auto whitespace-pre-wrap rounded-md bg-muted/30 p-2 text-xs leading-relaxed">
          {content}
        </pre>
      ) : null}

      {artifact.type === 'table' && artifact.preview_text ? (
        <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-muted/30 p-2 text-xs leading-relaxed">
          {artifact.preview_text}
        </pre>
      ) : null}

      {history.length > 0 ? (
        <details className="mt-2 rounded-md border p-2">
          <summary className="flex cursor-pointer items-center gap-1 text-xs text-muted-foreground">
            <History aria-hidden="true" className="h-3 w-3" />
            历史版本（{history.length} 个，只读）
          </summary>
          <ul className="mt-2 space-y-1">
            {history.map((item) => (
              <li key={`${item.version}-${item.savedAt}`} className="text-xs text-muted-foreground">
                版本 v{item.version} · 保存于 {formatDateTime(item.savedAt)}
                {item.downloadUrl ? (
                  <a
                    href={item.downloadUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="ml-2 text-primary underline-offset-4 hover:underline"
                  >
                    下载
                  </a>
                ) : null}
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <Dialog open={editOpen} onOpenChange={editMutation.isPending ? undefined : setEditOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>编辑并重新导出</DialogTitle>
            <DialogDescription>
              保存后会生成新版本并重新导出文件；任务状态与执行记录不受影响。
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3">
            <Field label="内容" htmlFor={`artifact-content-${artifact.artifact_id}`}>
              <Textarea
                id={`artifact-content-${artifact.artifact_id}`}
                rows={10}
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
              />
            </Field>
            <Field label="导出格式" htmlFor={`artifact-format-${artifact.artifact_id}`}>
              <Select
                id={`artifact-format-${artifact.artifact_id}`}
                value={format}
                onChange={(event) => setFormat(event.target.value as ArtifactRenderFormat)}
                options={FORMAT_OPTIONS}
              />
            </Field>
          </div>

          <DialogFooter className="gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => setEditOpen(false)}
              disabled={editMutation.isPending}
            >
              取消
            </Button>
            <Button
              type="button"
              disabled={editMutation.isPending || draft.trim().length === 0}
              onClick={() => editMutation.mutate({ content: draft, render_format: format })}
            >
              {editMutation.isPending ? '保存中…' : '保存并导出'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
