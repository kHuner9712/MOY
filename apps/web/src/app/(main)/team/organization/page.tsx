'use client';

import { ArrowLeft, Plus, Settings, ShieldCheck, Trash2 } from 'lucide-react';
import Link from 'next/link';
import * as React from 'react';

import { DepartmentTree } from '@/components/org/department-tree';
import { RolePermissionDialog } from '@/components/org/role-permission-dialog';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/feedback';
import { Field, Input } from '@/components/ui/input';
import { toast } from '@/components/ui/toast';
import { Tooltip } from '@/components/ui/tooltip';
import { QUERY_KEYS, useApiMutation, useApiQuery } from '@/hooks/use-api';
import { identityApi, identityExtApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { formatNumber, orPlaceholder } from '@/lib/format';
import { DEPARTMENT_MAX_LEVEL, type DepartmentNode } from '@/lib/types';

/** 可管理组织架构的角色 */
const MANAGER_ROLES: readonly string[] = ['owner', 'org_admin'];

/** 部门名称长度上限（与后端 1-64 一致） */
const DEPARTMENT_NAME_MAX_LENGTH = 64;

/** 表单模式 */
type FormMode = 'create' | 'rename' | null;

/**
 * 把部门树平铺成数组（用于按 ID 定位详情）。
 *
 * @param nodes - 部门树
 * @param acc - 累加器
 * @returns 平铺后的部门数组
 */
function flattenDepartments(nodes: DepartmentNode[], acc: DepartmentNode[] = []): DepartmentNode[] {
  for (const node of nodes) {
    acc.push(node);
    if (node.children.length > 0) {
      flattenDepartments(node.children, acc);
    }
  }
  return acc;
}

/**
 * 组织架构页（P-04）：左侧部门树 + 右侧部门详情。
 *
 * 数据链路：ID1 取树 → 选中节点在本地平铺结果里定位详情；
 * 写操作走 ID2（新建）/ ID3（重命名、拖拽移动）/ ID4（删除），
 * 成功后统一失效 `['identity','departments',orgId]`，因此**刷新后层级依然生效**。
 *
 * @returns 组织架构页元素
 */
export default function OrganizationPage(): React.JSX.Element {
  const profileQuery = useApiQuery(QUERY_KEYS.profile(), () => identityApi.profile());

  const organization = profileQuery.data?.organizations?.[0];
  const orgId = organization?.org_id ?? '';
  const canManage = organization ? MANAGER_ROLES.includes(organization.role) : false;

  const departmentsQuery = useApiQuery(
    QUERY_KEYS.departments(orgId),
    () => identityExtApi.listDepartments(orgId),
    { enabled: Boolean(orgId) },
  );

  const [selectedId, setSelectedId] = React.useState<string>('');
  const [formMode, setFormMode] = React.useState<FormMode>(null);
  const [formParent, setFormParent] = React.useState<DepartmentNode | null>(null);
  const [nameDraft, setNameDraft] = React.useState<string>('');
  const [nameError, setNameError] = React.useState<string>('');
  const [deleteTarget, setDeleteTarget] = React.useState<DepartmentNode | null>(null);
  const [roleDialogOpen, setRoleDialogOpen] = React.useState<boolean>(false);

  const roots = React.useMemo(
    () => departmentsQuery.data?.departments ?? [],
    [departmentsQuery.data],
  );
  const flatDepartments = React.useMemo(() => flattenDepartments(roots), [roots]);
  const selected = flatDepartments.find((item) => item.department_id === selectedId) ?? null;

  // 首次加载后默认选中第一个一级部门
  React.useEffect(() => {
    if (selectedId) return;
    const first = roots[0];
    if (first) setSelectedId(first.department_id);
  }, [roots, selectedId]);

  const createMutation = useApiMutation(
    (variables: { parentId: string; name: string }) =>
      identityExtApi.createDepartment(orgId, {
        name: variables.name,
        parent_id: variables.parentId,
      }),
    {
      invalidateKeys: [QUERY_KEYS.departments(orgId)],
      onSuccess: (data) => {
        toast.success('部门已创建');
        setSelectedId(data.department_id);
        setFormMode(null);
      },
      onError: (error) => toast.error(toBusinessMessage(error, 'department.level_exceeded')),
    },
  );

  const renameMutation = useApiMutation(
    (variables: { departmentId: string; name: string }) =>
      identityExtApi.updateDepartment(variables.departmentId, { name: variables.name }),
    {
      invalidateKeys: [QUERY_KEYS.departments(orgId)],
      onSuccess: () => {
        toast.success('部门名称已更新');
        setFormMode(null);
      },
      onError: (error) => toast.error(toBusinessMessage(error)),
    },
  );

  const moveMutation = useApiMutation(
    (variables: { departmentId: string; parentId: string }) =>
      identityExtApi.updateDepartment(variables.departmentId, { parent_id: variables.parentId }),
    {
      invalidateKeys: [QUERY_KEYS.departments(orgId)],
      onSuccess: () => toast.success('部门已移动'),
      // 14006 的默认文案是「部门下仍有成员」，移动冲突时用更贴切的上下文文案兜底
      onError: (error) => toast.error(toBusinessMessage(error, 'department.move_into_subtree')),
    },
  );

  const deleteMutation = useApiMutation(
    (departmentId: string) => identityExtApi.deleteDepartment(departmentId),
    {
      invalidateKeys: [QUERY_KEYS.departments(orgId)],
      onSuccess: () => {
        toast.success('部门已删除');
        setSelectedId('');
      },
      onError: (error) => toast.error(toBusinessMessage(error, 'department.delete_non_empty')),
    },
  );

  /** 打开「新建」表单 */
  const openCreate = (parent: DepartmentNode | null): void => {
    setFormParent(parent);
    setNameDraft('');
    setNameError('');
    setFormMode('create');
  };

  /** 打开「重命名」表单 */
  const openRename = (node: DepartmentNode): void => {
    setFormParent(node);
    setNameDraft(node.name);
    setNameError('');
    setFormMode('rename');
  };

  /** 提交表单（新建或重命名） */
  const submitForm = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const trimmed = nameDraft.trim();
    if (!trimmed) {
      setNameError('请填写部门名称');
      return;
    }
    setNameError('');
    if (formMode === 'create') {
      createMutation.mutate({ parentId: formParent?.department_id ?? '', name: trimmed });
      return;
    }
    if (formMode === 'rename' && formParent) {
      renameMutation.mutate({ departmentId: formParent.department_id, name: trimmed });
    }
  };

  // ---------------------------------------------------------------- 三态
  if (profileQuery.isLoading) {
    return (
      <div className="mx-auto max-w-6xl">
        <LoadingState label="正在加载组织信息…" />
      </div>
    );
  }

  if (profileQuery.error) {
    return (
      <div className="mx-auto max-w-6xl">
        <ErrorState
          message={toBusinessMessage(profileQuery.error)}
          onRetry={() => void profileQuery.refetch()}
        />
      </div>
    );
  }

  if (!organization) {
    return (
      <div className="mx-auto max-w-6xl">
        <EmptyState
          title="还没有加入任何企业"
          description="组织架构需要企业空间；请联系管理员确认你的成员身份后刷新页面"
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <Link
              href="/team"
              className="inline-flex items-center gap-1 rounded-md text-sm text-muted-foreground transition-colors hover:text-foreground"
            >
              <ArrowLeft className="h-4 w-4" />
              团队管理
            </Link>
          </div>
          <h2 className="text-xl font-semibold">组织架构</h2>
          <p className="text-sm text-muted-foreground">
            最多 {DEPARTMENT_MAX_LEVEL} 层；支持拖拽调整上下级关系，保存后立即生效。
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <Tooltip content="只有所有者与企业管理员可以配置角色权限" disabled={canManage}>
            <Button
              type="button"
              variant="outline"
              disabled={!canManage}
              onClick={() => setRoleDialogOpen(true)}
            >
              <ShieldCheck className="h-4 w-4" />
              角色与权限
            </Button>
          </Tooltip>
          <Tooltip content="只有所有者与企业管理员可以调整组织架构" disabled={canManage}>
            <span>
              <Button type="button" disabled={!canManage} onClick={() => openCreate(null)}>
                <Plus className="h-4 w-4" />
                新建一级部门
              </Button>
            </span>
          </Tooltip>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[20rem_1fr]">
        <section className="rounded-lg border p-3">
          <h3 className="mb-2 px-1 text-sm font-semibold">部门层级</h3>
          {departmentsQuery.isLoading ? (
            <LoadingState label="正在加载部门…" />
          ) : departmentsQuery.error ? (
            <ErrorState
              message={toBusinessMessage(departmentsQuery.error)}
              onRetry={() => void departmentsQuery.refetch()}
            />
          ) : roots.length === 0 ? (
            <EmptyState
              title="还没有部门"
              description="先建一个一级部门，再把成员挂到部门下，后续的授权与数据范围才有依据"
              action={
                canManage ? (
                  <Button type="button" size="sm" onClick={() => openCreate(null)}>
                    新建一级部门
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <DepartmentTree
              departments={roots}
              selectedId={selectedId}
              onSelect={(node) => setSelectedId(node.department_id)}
              onCreateChild={(parent) => openCreate(parent)}
              onMove={(departmentId, targetParentId) =>
                moveMutation.mutate({ departmentId, parentId: targetParentId })
              }
              canManage={canManage}
              moving={moveMutation.isPending}
            />
          )}
        </section>

        <section className="rounded-lg border p-4">
          {selected ? (
            <div className="space-y-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="space-y-1">
                  <h3 className="text-base font-semibold">{selected.name}</h3>
                  <p className="text-xs text-muted-foreground">
                    {selected.path_names.length > 0
                      ? selected.path_names.join(' / ')
                      : selected.name}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Tooltip content="只有所有者与企业管理员可以调整组织架构" disabled={canManage}>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={!canManage}
                      onClick={() => openRename(selected)}
                    >
                      <Settings className="h-4 w-4" />
                      重命名
                    </Button>
                  </Tooltip>
                  <Tooltip content="只有所有者与企业管理员可以删除部门" disabled={canManage}>
                    <Button
                      type="button"
                      variant="destructive"
                      size="sm"
                      disabled={!canManage}
                      onClick={() => setDeleteTarget(selected)}
                    >
                      <Trash2 className="h-4 w-4" />
                      删除
                    </Button>
                  </Tooltip>
                </div>
              </div>

              <dl className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-1">
                  <dt className="text-xs text-muted-foreground">层级</dt>
                  <dd className="text-sm tabular-nums">
                    第 {selected.level} 层 / 共 {DEPARTMENT_MAX_LEVEL} 层
                  </dd>
                </div>
                <div className="space-y-1">
                  <dt className="text-xs text-muted-foreground">负责人</dt>
                  <dd className="text-sm">{orPlaceholder(selected.manager_name, '未设置')}</dd>
                </div>
                <div className="space-y-1">
                  <dt className="text-xs text-muted-foreground">成员数</dt>
                  <dd className="text-sm tabular-nums">{formatNumber(selected.member_count)}</dd>
                </div>
                <div className="space-y-1">
                  <dt className="text-xs text-muted-foreground">下级部门</dt>
                  <dd className="text-sm tabular-nums">{selected.children.length} 个</dd>
                </div>
              </dl>

              <div className="flex items-center gap-2 border-t pt-3">
                <Tooltip
                  content={`组织层级最多 ${DEPARTMENT_MAX_LEVEL} 层，请调整后重试`}
                  disabled={selected.level < DEPARTMENT_MAX_LEVEL}
                >
                  <span>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={!canManage || selected.level >= DEPARTMENT_MAX_LEVEL}
                      onClick={() => openCreate(selected)}
                    >
                      <Plus className="h-4 w-4" />
                      新建下级部门
                    </Button>
                  </span>
                </Tooltip>
                <p className="text-xs text-muted-foreground">
                  把左侧任一部门拖到另一个部门上，即可调整上下级关系
                </p>
              </div>
            </div>
          ) : (
            <EmptyState
              title="选择一个部门查看详情"
              description="左侧点选部门即可查看层级、负责人与成员数，并可新建下级部门"
            />
          )}
        </section>
      </div>

      <Dialog
        open={formMode !== null}
        onOpenChange={(open) => {
          if (!open) setFormMode(null);
        }}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{formMode === 'rename' ? '重命名部门' : '新建部门'}</DialogTitle>
            <DialogDescription>
              {formMode === 'rename'
                ? '名称保存后立即生效，历史记录中的旧名称保持不变。'
                : formParent
                  ? `将在「${formParent.name}」下创建下级部门。`
                  : '将创建一个一级部门。'}
            </DialogDescription>
          </DialogHeader>
          <form className="space-y-4" onSubmit={submitForm}>
            <Field label="部门名称" htmlFor="department-name" required error={nameError}>
              <Input
                id="department-name"
                value={nameDraft}
                maxLength={DEPARTMENT_NAME_MAX_LENGTH}
                invalid={Boolean(nameError)}
                onChange={(event) => setNameDraft(event.target.value)}
                placeholder="例如：产品部"
                autoFocus
              />
            </Field>
            <DialogFooter className="gap-2">
              <Button type="button" variant="outline" onClick={() => setFormMode(null)}>
                取消
              </Button>
              <Button type="submit" disabled={createMutation.isPending || renameMutation.isPending}>
                {createMutation.isPending || renameMutation.isPending ? '保存中…' : '保存'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
        variant="danger"
        title="删除这个部门？"
        description="部门下仍有成员或子部门时无法删除，请先迁移成员并处理下级部门。"
        confirmLabel="删除"
        onConfirm={async () => {
          if (!deleteTarget) return;
          await deleteMutation.mutateAsync(deleteTarget.department_id);
          setDeleteTarget(null);
        }}
      >
        {deleteTarget ? (
          <p>
            {deleteTarget.name}（成员 {deleteTarget.member_count} 人，下级部门{' '}
            {deleteTarget.children.length} 个）
          </p>
        ) : null}
      </ConfirmDialog>

      <RolePermissionDialog
        open={roleDialogOpen}
        onOpenChange={setRoleDialogOpen}
        orgId={orgId}
        canManage={canManage}
      />
    </div>
  );
}
