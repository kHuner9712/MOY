'use client';

import { useRouter } from 'next/navigation';
import * as React from 'react';

import { MemoryCard } from '@/components/memory/memory-card';
import { MemoryEmpty } from '@/components/memory/memory-empty';
import { MemorySwitches } from '@/components/memory/memory-switches';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { ErrorState, LoadingState } from '@/components/ui/feedback';
import { Input } from '@/components/ui/input';
import { Pagination } from '@/components/ui/pagination';
import { toast } from '@/components/ui/toast';
import { QUERY_KEYS, useApiMutation, useApiQuery } from '@/hooks/use-api';
import { memoryApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import {
  MEMORY_CATEGORY_LABELS,
  MEMORY_DELETE_ALL_CONFIRM,
  MEMORY_EMPTY_COPY,
  MEMORY_DELETE_CONFIRM,
  MEMORY_DISABLED_BANNER,
  MEMORY_PAGE_DESCRIPTION,
  memoryCategoryDeleteConfirm,
  type MemoryEmptyKind,
} from '@/lib/memory-labels';
import { MEMORY_CATEGORIES, type MemoryCategory, type MemoryItem } from '@/lib/types';
import { cn } from '@/lib/utils';

/** 每页条数（与后端默认一致） */
const PAGE_SIZE = 20;

/** 记忆查询键前缀（删除/开关成功后整域失效） */
const MEMORY_KEY_PREFIX = ['memories'] as const;

/**
 * 空态判定：把「有没有数据」翻译成 6 套文案里的哪一种。
 *
 * @param options - 判定输入
 * @returns 空态种类
 */
function resolveEmptyKind(options: {
  enabled: boolean;
  category: MemoryCategory | '';
  justCleared: boolean;
}): MemoryEmptyKind {
  if (!options.enabled) return 'disabled';
  if (options.category) return 'filtered';
  if (options.justCleared) return 'cleared';
  return 'never';
}

/**
 * 「我的记忆」页（阶段四 T4-03.6，PRD §6.1）。
 *
 * 三态齐备：加载（骨架）、空（6 套）、错误（可重试）。
 *
 * 三条产品约束（PRD §4.4 / §4.5）：
 * 1. **看 / 删 / 关**三个动作全部在这里完成，删除是**不可撤销**的硬删除；
 * 2. 删除只影响「我会不会用它回答你」，**不动对话记录**——文案里必须写出来；
 * 3. 全站技术黑盒化：界面不出现任何内部机制名，只出现「了解你 / 记住 / 删除」这类说法。
 *
 * @returns 我的记忆页元素
 */
export default function MemoriesPage(): React.JSX.Element {
  const router = useRouter();
  const [category, setCategory] = React.useState<MemoryCategory | ''>('');
  const [page, setPage] = React.useState(1);
  const [pendingDelete, setPendingDelete] = React.useState<MemoryItem | null>(null);
  const [categoryDeleteOpen, setCategoryDeleteOpen] = React.useState(false);
  const [deleteAllOpen, setDeleteAllOpen] = React.useState(false);
  const [deleteAllKeyword, setDeleteAllKeyword] = React.useState('');
  const [justCleared, setJustCleared] = React.useState(false);

  const memoriesQuery = useApiQuery(
    QUERY_KEYS.memories({ category, page, page_size: PAGE_SIZE }),
    () => memoryApi.list({ category, page, page_size: PAGE_SIZE }),
    { staleTime: 5_000 },
  );

  const removeOne = useApiMutation((memoryId: string) => memoryApi.remove(memoryId), {
    invalidateKeys: [MEMORY_KEY_PREFIX],
    onSuccess: () => {
      toast.success('已删除，我不会再用这条信息回答你');
      setPendingDelete(null);
    },
    onError: (error) => {
      toast.error(toBusinessMessage(error));
    },
  });

  const removeMany = useApiMutation(
    (target: MemoryCategory | '') => memoryApi.removeMany(target),
    {
      invalidateKeys: [MEMORY_KEY_PREFIX],
      onSuccess: (data) => {
        setCategoryDeleteOpen(false);
        setDeleteAllOpen(false);
        setDeleteAllKeyword('');
        setJustCleared(data.category === '');
        toast.success(`已删除 ${data.deleted_count} 条信息`);
      },
      onError: (error) => {
        toast.error(toBusinessMessage(error));
      },
    },
  );

  const saveSettings = useApiMutation(
    (body: { enabled?: boolean; category_switches?: Record<string, boolean> }) =>
      memoryApi.updateSettings(body),
    {
      invalidateKeys: [MEMORY_KEY_PREFIX],
      onSuccess: (data) => {
        toast.success(data.enabled ? '已开启，我会重新从对话里了解你' : '已关闭，我不会再从对话里记东西');
      },
      onError: (error) => {
        toast.error(toBusinessMessage(error));
      },
    },
  );

  const data = memoriesQuery.data;
  const enabled = data?.enabled ?? true;
  const total = data?.meta.total ?? 0;

  // 删掉当前页最后一条后自动回退一页：否则用户会停在一个「空页」上，
  // 而实际上前面还有内容（三态门禁管不到这种"有数据但看不见"的形态）。
  React.useEffect(() => {
    if (data && data.items.length === 0 && page > 1 && total > 0) {
      setPage((current) => Math.max(1, current - 1));
    }
  }, [data, page, total]);
  const categorySwitches = data?.category_switches ?? {};
  const items = data?.items ?? [];
  const saving = saveSettings.isPending;
  const deleting = removeOne.isPending || removeMany.isPending;

  /** 切换筛选时回到第 1 页（否则会停在一个空的第 3 页） */
  const handleCategoryChange = (next: MemoryCategory | ''): void => {
    setCategory(next);
    setPage(1);
    setJustCleared(false);
  };

  if (memoriesQuery.isLoading) {
    return <LoadingState label="正在读取关于你的信息…" />;
  }

  if (memoriesQuery.isError) {
    // 错误态由**页面自身**渲染（三态门禁 G-18 的硬要求：不允许藏在两层子组件后面）
    return (
      <ErrorState
        message={MEMORY_EMPTY_COPY.failed.title}
        description={MEMORY_EMPTY_COPY.failed.description}
        onRetry={() => {
          void memoriesQuery.refetch();
        }}
      />
    );
  }

  if (!data) {
    return <MemoryEmpty kind="unavailable" onRetry={() => void memoriesQuery.refetch()} />;
  }

  const emptyKind = resolveEmptyKind({ enabled, category, justCleared });
  const showList = items.length > 0;

  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">我的记忆</h1>
        <p className="max-w-2xl text-sm text-muted-foreground">{MEMORY_PAGE_DESCRIPTION}</p>
      </header>

      {!enabled ? (
        <div
          role="status"
          className="rounded-lg border border-amber-300/60 bg-amber-50 px-4 py-3 text-sm text-amber-900"
        >
          {MEMORY_DISABLED_BANNER}
        </div>
      ) : null}

      <MemorySwitches
        settings={{
          enabled,
          category_switches: categorySwitches,
          updated_at: '',
        }}
        saving={saving}
        onToggleEnabled={(next) => saveSettings.mutate({ enabled: next })}
        onToggleCategory={(target, next) =>
          saveSettings.mutate({ category_switches: { [target]: next } })
        }
      />

      <Card>
        <CardHeader className="gap-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="space-y-1">
              <CardTitle className="text-base">关于你的信息</CardTitle>
              <CardDescription>共 {total} 条；删除后我不会再用它回答你。</CardDescription>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setCategoryDeleteOpen(true)}
                disabled={deleting || total === 0}
              >
                {category ? `删除「${MEMORY_CATEGORY_LABELS[category]}」的全部` : '删除某一类的全部'}
              </Button>
              <Button
                type="button"
                variant="destructive"
                size="sm"
                onClick={() => {
                  setDeleteAllKeyword('');
                  setDeleteAllOpen(true);
                }}
                disabled={deleting || total === 0}
              >
                删除我的全部记忆
              </Button>
            </div>
          </div>

          <div className="flex flex-wrap gap-2" role="group" aria-label="按类别筛选">
            <button
              type="button"
              onClick={() => handleCategoryChange('')}
              aria-pressed={category === ''}
              className={cn(
                'rounded-full border px-3 py-1 text-xs transition-colors',
                category === '' ? 'bg-primary text-primary-foreground' : 'hover:bg-accent',
              )}
            >
              全部
            </button>
            {MEMORY_CATEGORIES.map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => handleCategoryChange(item)}
                aria-pressed={category === item}
                className={cn(
                  'rounded-full border px-3 py-1 text-xs transition-colors',
                  category === item ? 'bg-primary text-primary-foreground' : 'hover:bg-accent',
                )}
              >
                {MEMORY_CATEGORY_LABELS[item]}
              </button>
            ))}
          </div>
        </CardHeader>

        <CardContent className="space-y-3">
          {showList ? (
            <>
              <ul className="space-y-3">
                {items.map((item) => (
                  <li key={item.memory_id}>
                    <MemoryCard
                      item={item}
                      onDelete={setPendingDelete}
                      deleting={deleting}
                    />
                  </li>
                ))}
              </ul>
              <Pagination
                page={page}
                pageSize={PAGE_SIZE}
                total={total}
                onPageChange={setPage}
                pageSizeOptions={[PAGE_SIZE]}
              />
            </>
          ) : (
            <MemoryEmpty
              kind={emptyKind}
              onRetry={() => void memoriesQuery.refetch()}
              onGoChat={() => router.push('/chat')}
              onEnable={() => saveSettings.mutate({ enabled: true })}
              enabling={saving}
            />
          )}
        </CardContent>
      </Card>

      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null);
        }}
        title={MEMORY_DELETE_CONFIRM.title}
        description={MEMORY_DELETE_CONFIRM.description}
        confirmLabel={MEMORY_DELETE_CONFIRM.confirmLabel}
        variant="danger"
        loading={removeOne.isPending}
        onConfirm={async () => {
          if (!pendingDelete) return;
          await removeOne.mutateAsync(pendingDelete.memory_id);
        }}
      >
        <p className="rounded-md bg-muted px-3 py-2 text-sm">{pendingDelete?.content}</p>
      </ConfirmDialog>

      <ConfirmDialog
        open={categoryDeleteOpen}
        onOpenChange={setCategoryDeleteOpen}
        title={
          category
            ? memoryCategoryDeleteConfirm(MEMORY_CATEGORY_LABELS[category]).title
            : '删除全部记忆？'
        }
        description={
          category
            ? memoryCategoryDeleteConfirm(MEMORY_CATEGORY_LABELS[category]).description
            : MEMORY_DELETE_ALL_CONFIRM.description
        }
        confirmLabel={MEMORY_DELETE_CONFIRM.confirmLabel}
        variant="danger"
        loading={removeMany.isPending}
        onConfirm={async () => {
          await removeMany.mutateAsync(category);
        }}
      />

      <ConfirmDialog
        open={deleteAllOpen}
        onOpenChange={(open) => {
          setDeleteAllOpen(open);
          if (!open) setDeleteAllKeyword('');
        }}
        title={MEMORY_DELETE_ALL_CONFIRM.title}
        description={MEMORY_DELETE_ALL_CONFIRM.description}
        confirmLabel={MEMORY_DELETE_ALL_CONFIRM.confirmLabel}
        variant="danger"
        loading={removeMany.isPending}
        onConfirm={async () => {
          if (deleteAllKeyword !== MEMORY_DELETE_ALL_CONFIRM.keyword) {
            toast.error(`请输入「${MEMORY_DELETE_ALL_CONFIRM.keyword}」以确认`);
            throw new Error('delete-all keyword mismatch');
          }
          await removeMany.mutateAsync('');
        }}
      >
        <label className="block space-y-2 text-sm">
          <span>
            请输入「{MEMORY_DELETE_ALL_CONFIRM.keyword}」以确认
          </span>
          <Input
            value={deleteAllKeyword}
            onChange={(event) => setDeleteAllKeyword(event.target.value)}
            aria-label={`输入${MEMORY_DELETE_ALL_CONFIRM.keyword}以确认`}
            placeholder={MEMORY_DELETE_ALL_CONFIRM.keyword}
          />
          {deleteAllKeyword && deleteAllKeyword !== MEMORY_DELETE_ALL_CONFIRM.keyword ? (
            <span className="text-xs text-destructive">
              输入的{`「${MEMORY_DELETE_ALL_CONFIRM.keyword}」`}不匹配，请重新输入
            </span>
          ) : null}
        </label>
      </ConfirmDialog>
    </div>
  );
}
