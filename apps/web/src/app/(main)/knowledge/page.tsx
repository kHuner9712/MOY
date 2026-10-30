'use client';

import { ArrowRight, BookOpen, Plus, Search } from 'lucide-react';
import Link from 'next/link';
import * as React from 'react';

import { StatusBadge } from '@/components/common/status-badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/feedback';
import { Field, Input, Textarea } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { toast } from '@/components/ui/toast';
import { QUERY_KEYS, useApiMutation, useApiQuery } from '@/hooks/use-api';
import { knowledgeApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { formatDate, formatNumber, orPlaceholder } from '@/lib/format';
import type { ChunkPolicy, Visibility } from '@/lib/types';

/** 名称长度上限 */
const NAME_MAX_LENGTH = 128;

/** 我的权限徽标文案 */
const MY_ROLE_LABELS: Readonly<Record<string, string>> = {
  owner: '所有者',
  editor: '可上传与管理',
  viewer: '可提问',
};

/** 内容切分精细度选项（不暴露任何处理参数） */
const CHUNK_POLICY_OPTIONS: Array<{ value: ChunkPolicy; label: string }> = [
  { value: 'default', label: '标准（处理更快，适合手册与说明文档）' },
  { value: 'fine', label: '精细（条款定位更准，适合制度与合同）' },
];

/** 可见范围选项 */
const VISIBILITY_OPTIONS: Array<{ value: Visibility; label: string }> = [
  { value: 'organization', label: '企业可见（所有成员都能提问）' },
  { value: 'restricted', label: '指定成员可见（按授权名单）' },
  { value: 'private', label: '仅自己可见' },
];

/**
 * 知识库列表页（P-06 深化）。
 *
 * 相比阶段一：卡片增加「可见范围 / 我的权限 / 内容切分精细度 / 文档数」四类徽标，
 * 卡片整体可点进详情；空态区分「还没有知识库」与「筛选无结果」。
 *
 * @returns 知识库列表页元素
 */
export default function KnowledgePage(): React.JSX.Element {
  const [keyword, setKeyword] = React.useState<string>('');
  const [createOpen, setCreateOpen] = React.useState<boolean>(false);
  const [name, setName] = React.useState<string>('');
  const [description, setDescription] = React.useState<string>('');
  const [chunkPolicy, setChunkPolicy] = React.useState<ChunkPolicy>('default');
  const [visibility, setVisibility] = React.useState<Visibility>('organization');
  const [nameError, setNameError] = React.useState<string>('');

  const params = { page: 1, page_size: 100, keyword: keyword.trim() };
  const basesQuery = useApiQuery(QUERY_KEYS.knowledgeBases(params), () =>
    knowledgeApi.listBases(params),
  );

  const createMutation = useApiMutation(
    (variables: {
      name: string;
      description: string;
      chunkPolicy: ChunkPolicy;
      visibility: Visibility;
    }) =>
      knowledgeApi.createBase({
        name: variables.name,
        description: variables.description,
        chunk_policy: variables.chunkPolicy,
        visibility: variables.visibility,
      }),
    {
      invalidateKeys: [QUERY_KEYS.knowledgeBases()],
      onSuccess: () => {
        toast.success('知识库已创建，可以上传资料了');
        setCreateOpen(false);
        setName('');
        setDescription('');
        setChunkPolicy('default');
        setVisibility('organization');
      },
      onError: (error) => toast.error(toBusinessMessage(error)),
    },
  );

  const handleCreate = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setNameError('请填写知识库名称');
      return;
    }
    setNameError('');
    createMutation.mutate({
      name: trimmed,
      description: description.trim(),
      chunkPolicy,
      visibility,
    });
  };

  const items = basesQuery.data?.items ?? [];
  const hasFilter = Boolean(keyword.trim());

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative sm:max-w-xs">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            placeholder="搜索知识库名称"
            aria-label="搜索知识库名称"
            className="pl-9"
          />
        </div>
        <div className="flex items-center gap-2">
          <Button type="button" onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4" />
            新建知识库
          </Button>
        </div>
      </div>

      {basesQuery.isLoading ? (
        <LoadingState label="正在加载知识库…" />
      ) : basesQuery.error ? (
        <ErrorState
          message={toBusinessMessage(basesQuery.error)}
          onRetry={() => void basesQuery.refetch()}
        />
      ) : items.length === 0 ? (
        <EmptyState
          title={hasFilter ? '没有符合条件的知识库' : '还没有知识库'}
          description={
            hasFilter
              ? '换个关键字再看，也可以清空搜索查看全部知识库'
              : '创建第一个知识库并上传资料，即可开始基于资料的问答'
          }
          action={
            hasFilter ? (
              <Button type="button" variant="outline" onClick={() => setKeyword('')}>
                清空搜索
              </Button>
            ) : (
              <Button type="button" onClick={() => setCreateOpen(true)}>
                新建知识库
              </Button>
            )
          }
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((base) => (
            <Card key={base.base_id} className="flex flex-col transition-shadow hover:shadow-md">
              <CardHeader>
                <div className="flex items-start justify-between gap-2">
                  <CardTitle className="truncate" title={base.name}>
                    {base.name}
                  </CardTitle>
                  <BookOpen aria-hidden="true" className="h-4 w-4 shrink-0 text-muted-foreground" />
                </div>
                <CardDescription className="line-clamp-2 min-h-[2.5rem]">
                  {orPlaceholder(base.description, '还没有填写描述')}
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-1 flex-col justify-between gap-3">
                <div className="flex flex-wrap gap-1.5">
                  <StatusBadge status={base.visibility} />
                  <span className="rounded-full border px-2.5 py-0.5 text-xs text-muted-foreground">
                    {MY_ROLE_LABELS[base.my_role ?? 'viewer'] ?? '可提问'}
                  </span>
                  <span className="rounded-full border px-2.5 py-0.5 text-xs text-muted-foreground">
                    {base.chunk_policy === 'fine' ? '精细切分' : '标准切分'}
                  </span>
                  <span className="rounded-full border px-2.5 py-0.5 text-xs text-muted-foreground tabular-nums">
                    {formatNumber(base.document_count)} 份文档
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">
                    更新于 {formatDate(base.updated_at ?? base.created_at)}
                  </span>
                  <Button asChild variant="ghost" size="sm">
                    <Link href={`/knowledge/${base.base_id}`}>
                      进入
                      <ArrowRight className="h-3.5 w-3.5" />
                    </Link>
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Dialog open={createOpen} onOpenChange={createMutation.isPending ? undefined : setCreateOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>新建知识库</DialogTitle>
            <DialogDescription>
              知识库用于沉淀团队资料，回答时会自动标注引用来源。
            </DialogDescription>
          </DialogHeader>
          <form className="space-y-4" onSubmit={handleCreate}>
            <Field label="名称" htmlFor="new-base-name" required error={nameError}>
              <Input
                id="new-base-name"
                value={name}
                maxLength={NAME_MAX_LENGTH}
                invalid={Boolean(nameError)}
                onChange={(event) => setName(event.target.value)}
                placeholder="例如：人事制度库"
                autoFocus
              />
            </Field>
            <Field
              label="描述"
              htmlFor="new-base-description"
              hint="用于在列表里说明这个库放什么资料"
            >
              <Textarea
                id="new-base-description"
                value={description}
                rows={3}
                maxLength={1024}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="例如：员工手册、考勤与假期制度"
              />
            </Field>
            <Field label="可见范围" htmlFor="new-base-visibility">
              <Select
                id="new-base-visibility"
                value={visibility}
                onChange={(event) => setVisibility(event.target.value as Visibility)}
                options={VISIBILITY_OPTIONS}
              />
            </Field>
            <Field
              label="内容切分精细度"
              htmlFor="new-base-chunk"
              hint="只影响之后上传的文档，可在库设置里随时调整"
            >
              <Select
                id="new-base-chunk"
                value={chunkPolicy}
                onChange={(event) => setChunkPolicy(event.target.value as ChunkPolicy)}
                options={CHUNK_POLICY_OPTIONS}
              />
            </Field>
            <DialogFooter className="gap-2 pt-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => setCreateOpen(false)}
                disabled={createMutation.isPending}
              >
                取消
              </Button>
              <Button type="submit" disabled={createMutation.isPending}>
                {createMutation.isPending ? '创建中…' : '创建'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
