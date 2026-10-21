'use client';

import { Trash2 } from 'lucide-react';
import * as React from 'react';

import { StatusBadge } from '@/components/common/status-badge';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { ErrorState } from '@/components/ui/feedback';
import { CompactSelect } from '@/components/ui/select';
import { Input } from '@/components/ui/input';
import { Pagination } from '@/components/ui/pagination';
import { Progress } from '@/components/ui/progress';
import {
  Table,
  TableBody,
  TableCell,
  TableEmpty,
  TableHead,
  TableHeader,
  TableLoading,
  TableRow,
  TableSortHead,
} from '@/components/ui/table';
import { Tooltip } from '@/components/ui/tooltip';
import { formatDateTime, formatFileSize, formatNumber } from '@/lib/format';
import type { DocumentStage, KnowledgeDocumentItem } from '@/lib/types';

/** 文档清单筛选条件（与页面 URL 无关，由页面持有） */
export interface DocumentFilters {
  /** 文档名称关键字 */
  keyword: string;
  /** 处理阶段筛选；空串表示全部 */
  stage: DocumentStage | '';
  /** 排序串，形如 `uploaded_at:desc` */
  sort: string;
}

/** 表格列数（空态/加载态占位用） */
const COLUMN_COUNT = 8;

/** 排序串取值 */
const SORT_UPLOADED_DESC = 'uploaded_at:desc';
const SORT_UPLOADED_ASC = 'uploaded_at:asc';
const SORT_NAME_ASC = 'file_name:asc';
const SORT_NAME_DESC = 'file_name:desc';

/** 处理阶段筛选项（文案为业务语言） */
const STAGE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: '全部阶段' },
  { value: 'uploaded', label: '已上传' },
  { value: 'parsing', label: '正在读取内容' },
  { value: 'preparing', label: '正在准备内容' },
  { value: 'completed', label: '可使用' },
  { value: 'failed', label: '处理失败' },
];

/** 文档表格属性 */
export interface DocumentTableProps {
  /** 当前页数据 */
  items: KnowledgeDocumentItem[];
  /** 总条数（分页用） */
  total: number;
  /** 当前页（1 起） */
  page: number;
  /** 每页条数 */
  pageSize: number;
  /** 是否加载中 */
  loading: boolean;
  /** 业务语言错误文案；空串表示无错误 */
  errorMessage: string;
  /** 当前筛选条件 */
  filters: DocumentFilters;
  /** 筛选条件变更 */
  onFiltersChange: (next: DocumentFilters) => void;
  /** 翻页 */
  onPageChange: (page: number) => void;
  /** 每页条数变更 */
  onPageSizeChange: (pageSize: number) => void;
  /** 出错重试 */
  onRetry: () => void;
  /** 删除文档（内部二次确认后调用） */
  onDelete: (document: KnowledgeDocumentItem) => Promise<void>;
  /** 是否有管理权限（无权限时删除按钮置灰并解释原因） */
  canManage: boolean;
}

/**
 * 文档表格：8 列（文档名 / 大小 / 内容片段数 / 处理进度 / 状态 / 上传人 / 上传时间 / 操作）。
 *
 * 三态齐备：`loading` → 骨架行；`errorMessage` → 错误态 + 重试；
 * 空数据区分「还没有文档」与「筛选无结果」两种文案。
 *
 * @param props - 组件属性
 * @returns 工具栏 + 表格 + 分页条
 */
export function DocumentTable({
  items,
  total,
  page,
  pageSize,
  loading,
  errorMessage,
  filters,
  onFiltersChange,
  onPageChange,
  onPageSizeChange,
  onRetry,
  onDelete,
  canManage,
}: DocumentTableProps): React.JSX.Element {
  const [pendingDelete, setPendingDelete] = React.useState<KnowledgeDocumentItem | null>(null);

  const hasFilter = Boolean(filters.keyword.trim()) || Boolean(filters.stage);
  const sortDirection: 'asc' | 'desc' | null =
    filters.sort === SORT_UPLOADED_ASC
      ? 'asc'
      : filters.sort === SORT_UPLOADED_DESC
        ? 'desc'
        : null;
  const nameSortDirection: 'asc' | 'desc' | null =
    filters.sort === SORT_NAME_ASC ? 'asc' : filters.sort === SORT_NAME_DESC ? 'desc' : null;

  /** 处理进度列：处理中显示进度条，完成/失败显示阶段文案 */
  const renderProgress = (item: KnowledgeDocumentItem): React.JSX.Element => {
    if (item.index_status === 'processing') {
      return (
        <div className="min-w-[9rem] space-y-1">
          <p className="text-xs text-muted-foreground">{item.stage_display_name || '解析中'}</p>
          <Progress value={item.progress} size="sm" showValue={false} label="文档处理进度" />
        </div>
      );
    }
    return <span className="text-sm">{item.stage_display_name || '已上传'}</span>;
  };

  /** 切换上传时间排序 */
  const toggleUploadedSort = (): void => {
    onFiltersChange({
      ...filters,
      sort: filters.sort === SORT_UPLOADED_DESC ? SORT_UPLOADED_ASC : SORT_UPLOADED_DESC,
    });
  };

  /** 切换文档名排序 */
  const toggleNameSort = (): void => {
    onFiltersChange({
      ...filters,
      sort: filters.sort === SORT_NAME_ASC ? SORT_NAME_DESC : SORT_NAME_ASC,
    });
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <Input
          value={filters.keyword}
          onChange={(event) => onFiltersChange({ ...filters, keyword: event.target.value })}
          placeholder="搜索文档名称"
          aria-label="搜索文档名称"
          className="sm:max-w-xs"
        />
        <div className="sm:w-44">
          <CompactSelect
            aria-label="按处理阶段筛选"
            value={filters.stage}
            onChange={(event) =>
              onFiltersChange({
                ...filters,
                stage: event.target.value as DocumentStage | '',
              })
            }
            options={STAGE_OPTIONS}
          />
        </div>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableSortHead direction={nameSortDirection} onSortChange={toggleNameSort}>
              文档名
            </TableSortHead>
            <TableHead>大小</TableHead>
            <TableHead>内容片段数</TableHead>
            <TableHead>处理进度</TableHead>
            <TableHead>状态</TableHead>
            <TableHead>上传人</TableHead>
            <TableSortHead direction={sortDirection} onSortChange={toggleUploadedSort}>
              上传时间
            </TableSortHead>
            <TableHead className="text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading ? (
            <TableLoading columns={COLUMN_COUNT} rows={5} />
          ) : errorMessage ? (
            <TableRow className="hover:bg-transparent">
              <TableCell colSpan={COLUMN_COUNT} className="p-0">
                <ErrorState message={errorMessage} onRetry={onRetry} />
              </TableCell>
            </TableRow>
          ) : items.length === 0 ? (
            <TableEmpty
              colSpan={COLUMN_COUNT}
              title={hasFilter ? '没有符合条件的文档' : '还没有上传文档'}
              description={
                hasFilter
                  ? '换个关键字或处理阶段再看，也可以清空筛选查看全部文档'
                  : '把制度、手册、合同拖到上方区域，上传后即可用于问答'
              }
            />
          ) : (
            items.map((item) => (
              <TableRow key={item.document_id}>
                <TableCell className="max-w-[16rem]">
                  <p className="truncate text-sm font-medium" title={item.file_name}>
                    {item.file_name}
                  </p>
                  {item.error_message ? (
                    <p className="mt-1 line-clamp-2 text-xs text-destructive">
                      {item.error_message}
                    </p>
                  ) : null}
                </TableCell>
                <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                  {formatFileSize(item.file_size)}
                </TableCell>
                <TableCell className="whitespace-nowrap text-sm tabular-nums">
                  {formatNumber(item.segment_count)}
                </TableCell>
                <TableCell>{renderProgress(item)}</TableCell>
                <TableCell>
                  <StatusBadge status={item.index_status} />
                </TableCell>
                <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                  {item.uploader_name || '—'}
                </TableCell>
                <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                  {formatDateTime(item.uploaded_at)}
                </TableCell>
                <TableCell className="text-right">
                  <Tooltip
                    content="只有知识库所有者或可上传与管理的成员才能删除文档"
                    disabled={canManage}
                  >
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={!canManage}
                      onClick={() => setPendingDelete(item)}
                      aria-label={`删除文档 ${item.file_name}`}
                    >
                      <Trash2 className="h-4 w-4" />
                      <span className="hidden sm:inline">删除</span>
                    </Button>
                  </Tooltip>
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>

      <Pagination
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
        disabled={loading}
      />

      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null);
        }}
        variant="danger"
        title="删除这份文档？"
        description="删除后该文档的内容将不再参与问答，且无法恢复。"
        confirmLabel="删除"
        onConfirm={async () => {
          if (!pendingDelete) return;
          await onDelete(pendingDelete);
          setPendingDelete(null);
        }}
      >
        {pendingDelete ? <p className="truncate">{pendingDelete.file_name}</p> : null}
      </ConfirmDialog>
    </div>
  );
}
