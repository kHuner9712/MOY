'use client';

import { UserPlus, X } from 'lucide-react';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/feedback';
import { Field, Textarea } from '@/components/ui/input';
import { Pagination } from '@/components/ui/pagination';
import { Select } from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { toast } from '@/components/ui/toast';
import { Tooltip } from '@/components/ui/tooltip';
import { QUERY_KEYS, useApiMutation, useApiQuery } from '@/hooks/use-api';
import { knowledgeExtApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { formatDateTime, orPlaceholder } from '@/lib/format';
import type { GrantItem, GrantPermission, GrantSubjectType } from '@/lib/types';

/** 单次最多授权对象数（后端上限 50，前端先拦一次，避免无谓请求） */
const MAX_SUBJECT_COUNT = 50;

/** 授权对象类型选项（业务语言） */
const SUBJECT_TYPE_OPTIONS: Array<{ value: GrantSubjectType; label: string }> = [
  { value: 'user', label: '成员' },
  { value: 'department', label: '部门' },
  { value: 'role', label: '角色' },
];

/** 授权级别选项（业务语言） */
const PERMISSION_OPTIONS: Array<{ value: GrantPermission; label: string }> = [
  { value: 'viewer', label: '可提问' },
  { value: 'editor', label: '可上传与管理' },
];

/** 授权对象类型的展示文案 */
const SUBJECT_TYPE_LABELS: Record<GrantSubjectType, string> = {
  user: '成员',
  department: '部门',
  role: '角色',
};

/** 授权级别的展示文案 */
const PERMISSION_LABELS: Record<GrantPermission, string> = {
  viewer: '可提问',
  editor: '可上传与管理',
};

/** 成员授权面板属性 */
export interface GrantPanelProps {
  /** 知识库 ID */
  baseId: string;
  /** 是否有授权管理权限 */
  canManage: boolean;
}

/**
 * 成员授权面板（KB5 / KB6 / KB7）。
 *
 * 设计要点：
 * - 无授权管理权限时，「添加授权」「撤销」按钮置灰，并用 hover 说明原因
 *   （只置灰不解释，用户会以为页面坏了）；
 * - 批量授权的对象 ID 以逗号或换行分隔，超过 50 个在提交前拦截；
 * - 授权变更后失效 `['kb', baseId, 'grants']`，列表自动刷新。
 *
 * @param props - 组件属性
 * @returns 授权清单 + 新增表单
 */
export function GrantPanel({ baseId, canManage }: GrantPanelProps): React.JSX.Element {
  const [page, setPage] = React.useState<number>(1);
  const pageSize = 20;

  const [subjectType, setSubjectType] = React.useState<GrantSubjectType>('user');
  const [subjectIdsText, setSubjectIdsText] = React.useState<string>('');
  const [permission, setPermission] = React.useState<GrantPermission>('viewer');
  const [pendingRevoke, setPendingRevoke] = React.useState<GrantItem | null>(null);

  const grantsQuery = useApiQuery(
    QUERY_KEYS.knowledgeGrants(baseId, page),
    () => knowledgeExtApi.listGrants(baseId, { page, page_size: pageSize }),
    { enabled: Boolean(baseId) },
  );

  const createGrant = useApiMutation(
    (variables: {
      subjectType: GrantSubjectType;
      subjectIds: string[];
      permission: GrantPermission;
    }) =>
      knowledgeExtApi.createGrants(baseId, {
        subject_type: variables.subjectType,
        subject_ids: variables.subjectIds,
        permission: variables.permission,
      }),
    {
      invalidateKeys: [QUERY_KEYS.knowledgeGrants(baseId, page), QUERY_KEYS.knowledgeBase(baseId)],
      onSuccess: (data) => {
        const skipped = data.skipped_count > 0 ? `，${data.skipped_count} 项已存在被跳过` : '';
        toast.success(`已添加 ${data.created_count} 项授权${skipped}`);
        setSubjectIdsText('');
      },
      onError: (error) => toast.error(toBusinessMessage(error, 'grant.guest_editor')),
    },
  );

  const revokeGrant = useApiMutation(
    (grantId: string) => knowledgeExtApi.revokeGrant(baseId, grantId),
    {
      invalidateKeys: [QUERY_KEYS.knowledgeGrants(baseId, page), QUERY_KEYS.knowledgeBase(baseId)],
      onSuccess: () => toast.success('该授权已撤销'),
      onError: (error) => toast.error(toBusinessMessage(error)),
    },
  );

  /** 解析并提交批量授权 */
  const handleSubmit = (): void => {
    const subjectIds = subjectIdsText
      .split(/[\s,，;；]+/)
      .map((item) => item.trim())
      .filter((item) => item.length > 0);

    if (subjectIds.length === 0) {
      toast.error('请填写要授权的成员、部门或角色');
      return;
    }
    if (subjectIds.length > MAX_SUBJECT_COUNT) {
      toast.error(`一次最多授权 ${MAX_SUBJECT_COUNT} 项，请分批操作`);
      return;
    }
    createGrant.mutate({ subjectType, subjectIds, permission });
  };

  const items = grantsQuery.data?.items ?? [];
  const total = grantsQuery.data?.meta.total ?? 0;
  const permissionHint = '只有知识库所有者或企业管理员可以调整成员授权';

  return (
    <div className="space-y-6">
      <section className="space-y-4 rounded-lg border p-4">
        <div className="flex items-center gap-2">
          <UserPlus aria-hidden="true" className="h-4 w-4 text-muted-foreground" />
          <h3 className="text-sm font-semibold">添加授权</h3>
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="授权对象" htmlFor="grant-subject-type">
            <Select
              id="grant-subject-type"
              value={subjectType}
              disabled={!canManage}
              onChange={(event) => setSubjectType(event.target.value as GrantSubjectType)}
              options={SUBJECT_TYPE_OPTIONS}
            />
          </Field>
          <Field label="授权级别" htmlFor="grant-permission">
            <Select
              id="grant-permission"
              value={permission}
              disabled={!canManage}
              onChange={(event) => setPermission(event.target.value as GrantPermission)}
              options={PERMISSION_OPTIONS}
            />
          </Field>
          <div className="flex items-end">
            <Tooltip content={permissionHint} disabled={canManage}>
              <Button
                type="button"
                onClick={handleSubmit}
                disabled={!canManage || createGrant.isPending}
                className="w-full"
              >
                {createGrant.isPending ? '提交中…' : '添加授权'}
              </Button>
            </Tooltip>
          </div>
        </div>
        <Field
          label="对象标识"
          htmlFor="grant-subject-ids"
          hint={`多个对象用逗号或换行分隔，一次最多 ${MAX_SUBJECT_COUNT} 项`}
        >
          <Textarea
            id="grant-subject-ids"
            value={subjectIdsText}
            disabled={!canManage}
            onChange={(event) => setSubjectIdsText(event.target.value)}
            placeholder="例如：usr_01H..., usr_02H..."
            rows={3}
          />
        </Field>
      </section>

      <section className="space-y-3">
        <h3 className="text-sm font-semibold">已授权的对象</h3>
        {grantsQuery.isLoading ? (
          <LoadingState label="正在加载授权清单…" />
        ) : grantsQuery.error ? (
          <ErrorState
            message={toBusinessMessage(grantsQuery.error)}
            onRetry={() => void grantsQuery.refetch()}
          />
        ) : items.length === 0 ? (
          <EmptyState
            title="还没有单独授权的成员"
            description="可见范围为「企业可见」时无需逐人授权；改为「指定成员可见」后再添加授权对象"
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>对象类型</TableHead>
                <TableHead>对象</TableHead>
                <TableHead>授权级别</TableHead>
                <TableHead>授权人</TableHead>
                <TableHead>授权时间</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((grant) => (
                <TableRow key={grant.grant_id}>
                  <TableCell className="text-sm">
                    {SUBJECT_TYPE_LABELS[grant.subject_type]}
                  </TableCell>
                  <TableCell className="max-w-[14rem] truncate text-sm">
                    {orPlaceholder(grant.subject_name, grant.subject_id)}
                  </TableCell>
                  <TableCell className="text-sm">{PERMISSION_LABELS[grant.permission]}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {orPlaceholder(grant.granted_by_name)}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                    {formatDateTime(grant.granted_at)}
                  </TableCell>
                  <TableCell className="text-right">
                    <Tooltip content={permissionHint} disabled={canManage}>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        disabled={!canManage}
                        onClick={() => setPendingRevoke(grant)}
                        aria-label={`撤销授权 ${grant.subject_name || grant.subject_id}`}
                      >
                        <X className="h-4 w-4" />
                        <span className="hidden sm:inline">撤销</span>
                      </Button>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}

        {total > pageSize ? (
          <Pagination
            page={page}
            pageSize={pageSize}
            total={total}
            onPageChange={setPage}
            disabled={grantsQuery.isLoading}
          />
        ) : null}
      </section>

      <ConfirmDialog
        open={pendingRevoke !== null}
        onOpenChange={(open) => {
          if (!open) setPendingRevoke(null);
        }}
        variant="danger"
        title="撤销这条授权？"
        description="撤销后对方将无法再通过该知识库提问或维护文档。"
        confirmLabel="撤销"
        onConfirm={async () => {
          if (!pendingRevoke) return;
          await revokeGrant.mutateAsync(pendingRevoke.grant_id);
          setPendingRevoke(null);
        }}
      >
        {pendingRevoke ? (
          <p>
            {SUBJECT_TYPE_LABELS[pendingRevoke.subject_type]}：
            {orPlaceholder(pendingRevoke.subject_name, pendingRevoke.subject_id)}
          </p>
        ) : null}
      </ConfirmDialog>
    </div>
  );
}
