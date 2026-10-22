'use client';

import { FileText, FolderOpen } from 'lucide-react';
import * as React from 'react';

import { StatusBadge } from '@/components/common/status-badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/feedback';
import { Select } from '@/components/ui/select';
import { QUERY_KEYS, useApiQuery } from '@/hooks/use-api';
import { knowledgeApi, knowledgeExtApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { formatFileSize, orPlaceholder } from '@/lib/format';
import Link from 'next/link';

/** 文件选择器属性 */
export interface FilePickerProps {
  /** 已选文档标识（`doc_` 前缀） */
  value: string[];
  /** 变更回调 */
  onChange: (documentIds: string[]) => void;
  /** 是否禁用 */
  disabled?: boolean;
  /** 按钮文案 */
  label?: string;
}

/** 单库文档最多取多少条（够选一次任务输入） */
const DOCUMENT_PAGE_SIZE = 50;

/**
 * 「选择已上传的文件」选择器。
 *
 * 设计依据（V1.0 裁定）：**不新增插件文件上传通道**。
 * 文件类输入的正确路径是「先在知识库上传，再让插件读」——这与 `personal.file.read`
 * 能力的语义一致，也避免同一件事做两遍。因此这里只做选择器：
 * 选知识库 → 勾文档 → 回传 `doc_` id 数组。
 *
 * @param props - 组件属性
 * @returns 选择器元素
 */
export function FilePicker({
  value,
  onChange,
  disabled = false,
  label = '选择文件',
}: FilePickerProps): React.JSX.Element {
  const [open, setOpen] = React.useState<boolean>(false);
  const [baseId, setBaseId] = React.useState<string>('');
  const [picked, setPicked] = React.useState<string[]>([]);
  const [nameMap, setNameMap] = React.useState<Record<string, string>>({});

  const basesQuery = useApiQuery(
    QUERY_KEYS.knowledgeBases({ page: 1, page_size: 50 }),
    () => knowledgeApi.listBases({ page: 1, page_size: 50 }),
    { enabled: open },
  );
  const bases = React.useMemo(() => basesQuery.data?.items ?? [], [basesQuery.data]);

  // 打开时默认选中第一个知识库，少一次点击
  React.useEffect(() => {
    if (!open || baseId) return;
    const first = bases[0];
    if (first) setBaseId(first.base_id);
  }, [open, baseId, bases]);

  const documentsQuery = useApiQuery(
    QUERY_KEYS.knowledgeDocuments(baseId, { page: 1, page_size: DOCUMENT_PAGE_SIZE }),
    () => knowledgeExtApi.listDocuments(baseId, { page: 1, page_size: DOCUMENT_PAGE_SIZE }),
    { enabled: open && Boolean(baseId) },
  );
  const documents = documentsQuery.data?.items ?? [];

  const openPicker = (): void => {
    setPicked(value);
    setOpen(true);
  };

  const toggle = (documentId: string, fileName: string): void => {
    setPicked((prev) =>
      prev.includes(documentId)
        ? prev.filter((item) => item !== documentId)
        : [...prev, documentId],
    );
    setNameMap((prev) => ({ ...prev, [documentId]: fileName }));
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" variant="outline" disabled={disabled} onClick={openPicker}>
          <FolderOpen className="h-4 w-4" />
          {label}
        </Button>
        <span className="text-xs text-muted-foreground">
          {value.length > 0 ? `已选择 ${value.length} 个文件` : '还没有选择文件'}
        </span>
      </div>

      {value.length > 0 ? (
        <ul className="space-y-1">
          {value.map((documentId) => (
            <li key={documentId} className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <FileText aria-hidden="true" className="h-3 w-3" />
              {orPlaceholder(nameMap[documentId], '已选择的文件')}
            </li>
          ))}
        </ul>
      ) : null}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>选择已上传的文件</DialogTitle>
            <DialogDescription>
              插件会读取你本人上传的文件；文件需要先上传到知识库，这里只做选择。
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3">
            {basesQuery.isLoading ? (
              <LoadingState label="正在加载知识库…" />
            ) : basesQuery.error ? (
              <ErrorState
                message={toBusinessMessage(basesQuery.error)}
                onRetry={() => void basesQuery.refetch()}
              />
            ) : bases.length === 0 ? (
              <EmptyState
                title="还没有上传文件"
                description="先到知识库上传一份文件，再回到这里选择"
                action={
                  <Button asChild size="sm">
                    <Link href="/knowledge">去上传</Link>
                  </Button>
                }
              />
            ) : (
              <>
                <div className="sm:w-64">
                  <Select
                    aria-label="选择知识库"
                    value={baseId}
                    onChange={(event) => setBaseId(event.target.value)}
                    options={bases.map((base) => ({ value: base.base_id, label: base.name }))}
                  />
                </div>

                {documentsQuery.isLoading ? (
                  <LoadingState label="正在加载文档…" />
                ) : documentsQuery.error ? (
                  <ErrorState
                    message={toBusinessMessage(documentsQuery.error)}
                    onRetry={() => void documentsQuery.refetch()}
                  />
                ) : documents.length === 0 ? (
                  <EmptyState
                    title="这个知识库还没有文件"
                    description="换一个知识库，或先去上传文件"
                    action={
                      <Button asChild size="sm" variant="outline">
                        <Link href="/knowledge">去上传</Link>
                      </Button>
                    }
                  />
                ) : (
                  <ul className="max-h-72 space-y-1 overflow-y-auto rounded-md border p-2">
                    {documents.map((document) => {
                      const checked = picked.includes(document.document_id);
                      return (
                        <li key={document.document_id}>
                          <label className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-accent">
                            <input
                              type="checkbox"
                              className="h-4 w-4 cursor-pointer accent-primary"
                              checked={checked}
                              onChange={() => toggle(document.document_id, document.file_name)}
                            />
                            <span className="min-w-0 flex-1 truncate">{document.file_name}</span>
                            <span className="shrink-0 text-xs text-muted-foreground">
                              {formatFileSize(document.file_size)}
                            </span>
                            <StatusBadge
                              status={document.index_status}
                              label={document.index_status === 'completed' ? '可读取' : undefined}
                            />
                          </label>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </>
            )}
          </div>

          <DialogFooter className="gap-2">
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              取消
            </Button>
            <Button
              type="button"
              onClick={() => {
                onChange(picked);
                setOpen(false);
              }}
            >
              确定（{picked.length} 个）
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
