'use client';

import { ArrowLeft, Settings, Trash2, Upload } from 'lucide-react';
import { useParams, useRouter } from 'next/navigation';
import * as React from 'react';

import { StatusBadge } from '@/components/common/status-badge';
import { BaseSettingsDialog } from '@/components/knowledge/base-settings-dialog';
import { DocumentTable, type DocumentFilters } from '@/components/knowledge/document-table';
import { GrantPanel } from '@/components/knowledge/grant-panel';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { ErrorState, LoadingState } from '@/components/ui/feedback';
import { TabPanel, Tabs, useUrlTab } from '@/components/ui/tabs';
import { toast } from '@/components/ui/toast';
import { Tooltip } from '@/components/ui/tooltip';
import { QUERY_KEYS, useApiMutation, useApiQuery } from '@/hooks/use-api';
import { BusinessError, knowledgeApi, knowledgeExtApi } from '@/lib/api-client';
import { messageOf, toBusinessMessage } from '@/lib/business-errors';
import { formatDateTime, orPlaceholder } from '@/lib/format';
import type { DocumentStage, KnowledgeBaseDetail, KnowledgeDocumentItem } from '@/lib/types';

/** 允许上传的扩展名（与适配层白名单一致） */
const ALLOWED_EXTENSIONS: readonly string[] = ['pdf', 'docx', 'xlsx', 'md', 'txt'];

/** 单文件上限（50MB） */
const MAX_FILE_SIZE = 50 * 1024 * 1024;

/** 处理中轮询间隔（毫秒）：全部进入「可使用 / 处理失败」后停止 */
const POLL_INTERVAL_MS = 3000;

/** 知识库列表最多取多少条用于定位详情（后端单页上限 200） */
const BASE_LOOKUP_PAGE_SIZE = 200;

/** 我的权限徽标文案 */
const MY_ROLE_LABELS: Readonly<Record<string, string>> = {
  owner: '我的权限：所有者',
  editor: '我的权限：可上传与管理',
  viewer: '我的权限：可提问',
};

/** 默认筛选条件 */
const DEFAULT_FILTERS: DocumentFilters = { keyword: '', stage: '', sort: 'uploaded_at:desc' };

/**
 * 读取知识库详情。
 *
 * 说明：后端未提供「单库详情」端点（架构文档 §4.2），详情页按 P-01 的依赖清单
 * 复用 R2 列表接口并按 `base_id` 定位；列表里找不到即等价于「已删除」（11001 口径）。
 *
 * @param baseId - 知识库 ID
 * @returns 知识库条目
 */
async function fetchBaseDetail(baseId: string): Promise<KnowledgeBaseDetail> {
  const page = await knowledgeApi.listBases({ page: 1, page_size: BASE_LOOKUP_PAGE_SIZE });
  const found = page.items.find((item) => item.base_id === baseId);
  if (!found) {
    throw new BusinessError(11001, messageOf(11001));
  }
  return found;
}

/**
 * 知识库详情页（P-01）。
 *
 * 三个标签通过 `?tab=` 同步到地址栏，链接可分享、刷新不丢页；
 * 文档清单按 3 秒轮询处理进度，全部结束自动停止；
 * 上传成功与授权变更后失效 `['kb', baseId]` 前缀键，页头与列表一起刷新。
 *
 * @returns 知识库详情页元素
 */
export default function KnowledgeBaseDetailPage(): React.JSX.Element {
  const params = useParams<{ baseId: string }>();
  const baseId = params.baseId;
  const router = useRouter();
  const [activeTab, setActiveTab] = useUrlTab('tab', 'documents');

  const [filters, setFilters] = React.useState<DocumentFilters>(DEFAULT_FILTERS);
  const [page, setPage] = React.useState<number>(1);
  const [pageSize, setPageSize] = React.useState<number>(20);
  const [isDragging, setIsDragging] = React.useState<boolean>(false);
  const [uploadingCount, setUploadingCount] = React.useState<number>(0);
  const [settingsOpen, setSettingsOpen] = React.useState<boolean>(false);
  const [deleteOpen, setDeleteOpen] = React.useState<boolean>(false);
  const [polling, setPolling] = React.useState<boolean>(false);

  const baseQuery = useApiQuery(QUERY_KEYS.knowledgeBase(baseId), () => fetchBaseDetail(baseId), {
    enabled: Boolean(baseId),
  });

  const documentFilters = {
    page,
    page_size: pageSize,
    keyword: filters.keyword.trim(),
    stage: filters.stage === '' ? undefined : (filters.stage as DocumentStage),
    sort: filters.sort,
  };

  const documentsQuery = useApiQuery(
    QUERY_KEYS.knowledgeDocuments(baseId, documentFilters),
    () => knowledgeExtApi.listDocuments(baseId, documentFilters),
    { enabled: Boolean(baseId), refetchInterval: polling ? POLL_INTERVAL_MS : false },
  );

  const documentItems = documentsQuery.data?.items ?? [];

  // 只要还有文档在解析/准备阶段就继续轮询，全部结束即停止
  React.useEffect(() => {
    const items = documentsQuery.data?.items ?? [];
    setPolling(items.some((item) => item.index_status === 'processing'));
  }, [documentsQuery.data]);

  /** 上传单个文件（格式/大小本地先校验） */
  const uploadFile = async (file: File): Promise<void> => {
    const extension = file.name.includes('.')
      ? (file.name.split('.').pop() ?? '').toLowerCase()
      : '';
    if (!ALLOWED_EXTENSIONS.includes(extension)) {
      toast.error(`${file.name} 的格式无法处理，请另存为 PDF、Word、Excel、Markdown 或 TXT 后重试`);
      return;
    }
    if (file.size > MAX_FILE_SIZE) {
      toast.error(`${file.name} 超出大小限制（上限 50MB）`);
      return;
    }
    setUploadingCount((count) => count + 1);
    try {
      await knowledgeApi.uploadDocument(baseId, file);
      toast.success(`${file.name} 已上传，正在读取内容`);
      setPolling(true);
      await documentsQuery.refetch();
    } catch (error) {
      toast.error(toBusinessMessage(error));
    } finally {
      setUploadingCount((count) => count - 1);
    }
  };

  /** 处理拖拽/选择进来的文件 */
  const handleFiles = async (files: FileList | null): Promise<void> => {
    if (!files || files.length === 0) return;
    const list = Array.from(files);
    for (const file of list) {
      // 串行上传：并发会让「解析中」提示互相覆盖，且不利于逐个定位失败文件
      await uploadFile(file);
    }
  };

  /** 删除文档：失败时用「处理中」上下文文案解释（11006），不暴露码值 */
  const deleteDocumentMutation = useApiMutation(
    (documentId: string) => knowledgeApi.deleteDocument(baseId, documentId),
    {
      invalidateKeys: [QUERY_KEYS.knowledgeDocuments(baseId), QUERY_KEYS.knowledgeBase(baseId)],
      onSuccess: () => toast.success('文档已删除'),
      onError: (error) => toast.error(toBusinessMessage(error, 'document.delete_processing')),
    },
  );

  const removeDocument = async (document: KnowledgeDocumentItem): Promise<void> => {
    await deleteDocumentMutation.mutateAsync(document.document_id);
  };

  const deleteBaseMutation = useApiMutation(() => knowledgeExtApi.deleteBase(baseId), {
    invalidateKeys: [QUERY_KEYS.knowledgeBases()],
    onSuccess: (data) => {
      toast.success(`知识库已删除，同时移除 ${data.deleted_document_count} 份文档`);
      router.push('/knowledge');
    },
    onError: (error) => toast.error(toBusinessMessage(error)),
  });

  const base = baseQuery.data;
  const myRole = base?.my_role ?? 'viewer';
  const canEdit = myRole === 'owner' || myRole === 'editor';
  const canManageGrants = myRole === 'owner';
  const canDelete = myRole === 'owner';
  const canUpload = canEdit;

  if (baseQuery.isLoading) {
    return (
      <div className="mx-auto max-w-6xl">
        <LoadingState label="正在加载知识库…" />
      </div>
    );
  }

  if (baseQuery.error || !base) {
    return (
      <div className="mx-auto max-w-6xl space-y-4">
        <Button variant="ghost" size="sm" onClick={() => router.push('/knowledge')}>
          <ArrowLeft className="h-4 w-4" />
          返回知识库列表
        </Button>
        <ErrorState
          message={baseQuery.error ? toBusinessMessage(baseQuery.error) : messageOf(11001)}
          onRetry={() => void baseQuery.refetch()}
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="space-y-3">
        <Button variant="ghost" size="sm" onClick={() => router.push('/knowledge')}>
          <ArrowLeft className="h-4 w-4" />
          返回知识库列表
        </Button>

        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-xl font-semibold">{base.name}</h2>
              <StatusBadge status={base.visibility} />
              <span className="rounded-full border px-2.5 py-0.5 text-xs text-muted-foreground">
                {MY_ROLE_LABELS[myRole] ?? '我的权限：可提问'}
              </span>
            </div>
            <p className="max-w-2xl text-sm text-muted-foreground">
              {orPlaceholder(base.description, '还没有填写描述')}
            </p>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            <Tooltip content="只有所有者或可上传与管理的成员可以修改设置" disabled={canEdit}>
              <Button
                type="button"
                variant="outline"
                disabled={!canEdit}
                onClick={() => setSettingsOpen(true)}
              >
                <Settings className="h-4 w-4" />
                设置
              </Button>
            </Tooltip>
            <Tooltip content="只有知识库所有者可以删除知识库" disabled={canDelete}>
              <Button
                type="button"
                variant="destructive"
                disabled={!canDelete}
                onClick={() => setDeleteOpen(true)}
              >
                <Trash2 className="h-4 w-4" />
                删除
              </Button>
            </Tooltip>
          </div>
        </div>
      </div>

      <Tabs
        items={[
          { value: 'documents', label: '文档', count: base.document_count },
          { value: 'grants', label: '成员授权' },
          { value: 'settings', label: '基本信息' },
        ]}
        value={activeTab}
        onValueChange={setActiveTab}
        ariaLabel="知识库详情视图"
      />

      <TabPanel value="documents" activeValue={activeTab}>
        <div className="space-y-4">
          <div
            onDragOver={(event) => {
              event.preventDefault();
              if (canUpload) setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setIsDragging(false);
              if (!canUpload) return;
              void handleFiles(event.dataTransfer.files);
            }}
            className={`flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-8 text-center transition-colors ${
              isDragging ? 'border-primary bg-primary/5' : 'border-muted-foreground/25'
            }`}
          >
            <Upload aria-hidden="true" className="h-6 w-6 text-muted-foreground" />
            <p className="text-sm font-medium">把文件拖到这里，或选择文件上传</p>
            <p className="text-xs text-muted-foreground">
              支持 PDF、Word、Excel、Markdown、TXT，单个文件不超过 50MB
            </p>
            <Tooltip content="你没有上传该知识库文档的权限" disabled={canUpload}>
              <label
                className={`mt-2 inline-flex h-10 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground ${
                  canUpload ? 'cursor-pointer hover:bg-primary/90' : 'cursor-not-allowed opacity-50'
                }`}
              >
                选择文件
                <input
                  type="file"
                  multiple
                  className="sr-only"
                  accept=".pdf,.docx,.xlsx,.md,.txt"
                  disabled={!canUpload}
                  onChange={(event) => {
                    void handleFiles(event.target.files);
                    event.target.value = '';
                  }}
                />
              </label>
            </Tooltip>
            {uploadingCount > 0 ? (
              <p className="text-xs text-muted-foreground">正在上传 {uploadingCount} 个文件…</p>
            ) : null}
          </div>

          <DocumentTable
            items={documentItems}
            total={documentsQuery.data?.meta.total ?? 0}
            page={page}
            pageSize={pageSize}
            loading={documentsQuery.isLoading}
            errorMessage={documentsQuery.error ? toBusinessMessage(documentsQuery.error) : ''}
            filters={filters}
            onFiltersChange={(next) => {
              setFilters(next);
              setPage(1);
            }}
            onPageChange={setPage}
            onPageSizeChange={(size) => {
              setPageSize(size);
              setPage(1);
            }}
            onRetry={() => void documentsQuery.refetch()}
            onDelete={removeDocument}
            canManage={canEdit}
          />
        </div>
      </TabPanel>

      <TabPanel value="grants" activeValue={activeTab}>
        <GrantPanel baseId={baseId} canManage={canManageGrants} />
      </TabPanel>

      <TabPanel value="settings" activeValue={activeTab}>
        <dl className="grid gap-4 rounded-lg border p-4 sm:grid-cols-2">
          <div className="space-y-1">
            <dt className="text-xs text-muted-foreground">名称</dt>
            <dd className="text-sm font-medium">{base.name}</dd>
          </div>
          <div className="space-y-1">
            <dt className="text-xs text-muted-foreground">可见范围</dt>
            <dd className="text-sm">
              <StatusBadge status={base.visibility} />
            </dd>
          </div>
          <div className="space-y-1">
            <dt className="text-xs text-muted-foreground">内容切分精细度</dt>
            <dd className="text-sm">
              {base.chunk_policy === 'fine' ? '精细（条款定位更准）' : '标准（处理更快）'}
            </dd>
          </div>
          <div className="space-y-1">
            <dt className="text-xs text-muted-foreground">文档数</dt>
            <dd className="text-sm tabular-nums">{base.document_count}</dd>
          </div>
          <div className="space-y-1">
            <dt className="text-xs text-muted-foreground">创建时间</dt>
            <dd className="text-sm">{formatDateTime(base.created_at)}</dd>
          </div>
          <div className="space-y-1">
            <dt className="text-xs text-muted-foreground">最近更新</dt>
            <dd className="text-sm">{formatDateTime(base.updated_at ?? '')}</dd>
          </div>
          <div className="space-y-1 sm:col-span-2">
            <dt className="text-xs text-muted-foreground">描述</dt>
            <dd className="text-sm">{orPlaceholder(base.description, '还没有填写描述')}</dd>
          </div>
        </dl>
      </TabPanel>

      <BaseSettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        base={base}
        canEdit={canEdit}
      />

      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        variant="danger"
        title="删除这个知识库？"
        description="删除后库内文档将一并移除，且无法恢复。"
        confirmLabel="删除知识库"
        onConfirm={async () => {
          await deleteBaseMutation.mutateAsync(undefined);
        }}
      >
        <p>
          当前库内有 <span className="font-medium tabular-nums">{base.document_count}</span>{' '}
          份文档，删除后这些文档的内容将不再参与问答。
        </p>
      </ConfirmDialog>
    </div>
  );
}
