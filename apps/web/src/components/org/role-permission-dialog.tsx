'use client';

import { Pencil, ShieldCheck } from 'lucide-react';
import * as React from 'react';

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
import { toast } from '@/components/ui/toast';
import { Tooltip } from '@/components/ui/tooltip';
import { QUERY_KEYS, useApiMutation, useApiQuery } from '@/hooks/use-api';
import { identityApi, identityExtApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import type { RoleItem } from '@/lib/types';

/** 内置角色的展示顺序（矩阵列顺序稳定，便于用户形成肌肉记忆） */
const BUILTIN_ROLE_ORDER: readonly string[] = ['owner', 'org_admin', 'member', 'guest'];

/** 内置角色展示名 */
const BUILTIN_ROLE_LABELS: Readonly<Record<string, string>> = {
  owner: '所有者',
  org_admin: '企业管理员',
  member: '成员',
  guest: '访客',
};

/** 所有者角色标识（始终拥有全部权限，不允许取消） */
const OWNER_ROLE_KEY = 'owner';

/** 角色与权限弹窗属性 */
export interface RolePermissionDialogProps {
  /** 是否打开 */
  open: boolean;
  /** 开关回调 */
  onOpenChange: (open: boolean) => void;
  /** 组织 ID */
  orgId: string;
  /** 是否有角色管理权限 */
  canManage: boolean;
}

/** 单次保存的载荷 */
interface RolePayload {
  role_key: string;
  permissions: string[];
}

/**
 * 角色与权限矩阵弹窗（ID5 权限点目录 + ID6 角色列表 + U5 保存）。
 *
 * 口径：
 * - 20 个权限点按分组展示，列是角色（内置 4 个 + 自定义角色）；=「只读」
 * - 点「编辑」进入可改态；所有者一列始终锁定为全量权限（后端有同样的保护规则）；
 * - 保存只提交**有变化的角色**，避免无意义写入与无关的失败提示。
 *
 * @param props - 组件属性
 * @returns 矩阵弹窗
 */
export function RolePermissionDialog({
  open,
  onOpenChange,
  orgId,
  canManage,
}: RolePermissionDialogProps): React.JSX.Element {
  const permissionsQuery = useApiQuery(
    QUERY_KEYS.permissions(),
    () => identityExtApi.listPermissions(),
    {
      enabled: open,
    },
  );
  const rolesQuery = useApiQuery(QUERY_KEYS.roles(orgId), () => identityExtApi.listRoles(orgId), {
    enabled: open && Boolean(orgId),
  });

  const [draft, setDraft] = React.useState<Record<string, string[]>>({});
  const [editing, setEditing] = React.useState<boolean>(false);

  // 打开或角色数据变化时，用服务端最新值重建草稿
  React.useEffect(() => {
    if (!open) return;
    const items = rolesQuery.data?.items ?? [];
    const next: Record<string, string[]> = {};
    for (const role of items) {
      next[role.role_key] = [...role.permissions];
    }
    setDraft(next);
    setEditing(false);
  }, [open, rolesQuery.data]);

  const roleColumns = React.useMemo<RoleItem[]>(() => {
    const items = rolesQuery.data?.items ?? [];
    const orderOf = (roleKey: string): number => {
      const index = BUILTIN_ROLE_ORDER.indexOf(roleKey);
      return index === -1 ? BUILTIN_ROLE_ORDER.length : index;
    };
    return [...items].sort((left, right) => orderOf(left.role_key) - orderOf(right.role_key));
  }, [rolesQuery.data]);

  /** 勾选/取消某个角色的一项权限 */
  const togglePermission = (roleKey: string, permission: string): void => {
    if (!editing || roleKey === OWNER_ROLE_KEY) return;
    setDraft((prev) => {
      const current = prev[roleKey] ?? [];
      const exists = current.includes(permission);
      return {
        ...prev,
        [roleKey]: exists
          ? current.filter((item) => item !== permission)
          : [...current, permission],
      };
    });
  };

  /** 有变化的角色（所有者不参与保存） */
  const changedPayload = React.useMemo<RolePayload[]>(() => {
    const items = rolesQuery.data?.items ?? [];
    const payload: RolePayload[] = [];
    for (const role of items) {
      if (role.role_key === OWNER_ROLE_KEY) continue;
      const current = draft[role.role_key] ?? [];
      const original = new Set(role.permissions);
      const changed =
        current.length !== role.permissions.length || current.some((item) => !original.has(item));
      if (changed) {
        payload.push({ role_key: role.role_key, permissions: current });
      }
    }
    return payload;
  }, [draft, rolesQuery.data]);

  const saveMutation = useApiMutation(
    (payload: RolePayload[]) =>
      Promise.all(payload.map((item) => identityApi.upsertRole(orgId, item))),
    {
      invalidateKeys: [QUERY_KEYS.roles(orgId)],
      onSuccess: () => {
        toast.success('角色权限已保存');
        setEditing(false);
        onOpenChange(false);
      },
      onError: (error) => toast.error(toBusinessMessage(error)),
    },
  );

  const groups = permissionsQuery.data?.groups ?? [];
  const loading = permissionsQuery.isLoading || rolesQuery.isLoading;
  const loadError = permissionsQuery.error ?? rolesQuery.error;

  return (
    <Dialog open={open} onOpenChange={saveMutation.isPending ? undefined : onOpenChange}>
      <DialogContent className="max-w-4xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldCheck aria-hidden="true" className="h-4 w-4 text-primary" />
            角色与权限
          </DialogTitle>
          <DialogDescription>
            权限点按业务模块分组；所有者始终拥有全部权限，其余角色可逐项调整。
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <LoadingState label="正在加载权限点…" />
        ) : loadError ? (
          <ErrorState
            message={toBusinessMessage(loadError)}
            onRetry={() => {
              void permissionsQuery.refetch();
              void rolesQuery.refetch();
            }}
          />
        ) : groups.length === 0 || roleColumns.length === 0 ? (
          <EmptyState
            title="还没有可配置的权限点"
            description="权限点目录为空时无法调整角色，请联系平台管理员确认配置"
          />
        ) : (
          <div className="max-h-[60vh] overflow-auto rounded-lg border">
            <table className="w-full border-collapse text-sm">
              <thead className="sticky top-0 z-10 bg-muted/60 backdrop-blur">
                <tr>
                  <th className="min-w-[16rem] px-3 py-2 text-left text-xs font-medium text-muted-foreground">
                    权限点
                  </th>
                  {roleColumns.map((role) => (
                    <th
                      key={role.role_id}
                      className="whitespace-nowrap px-3 py-2 text-center text-xs font-medium text-muted-foreground"
                    >
                      {role.display_name || BUILTIN_ROLE_LABELS[role.role_key] || role.role_key}
                      <span className="mt-0.5 block font-normal text-[11px] text-muted-foreground/70">
                        {role.is_builtin ? '内置' : '自定义'}
                      </span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {groups.map((group) => (
                  <React.Fragment key={group.group}>
                    <tr className="bg-muted/30">
                      <td
                        colSpan={roleColumns.length + 1}
                        className="px-3 py-1.5 text-xs font-semibold text-muted-foreground"
                      >
                        {group.group_name || group.group}
                      </td>
                    </tr>
                    {group.permissions.map((permission) => (
                      <tr key={permission.permission} className="border-t">
                        <td className="px-3 py-2">
                          <p className="font-medium">{permission.display_name}</p>
                          {permission.description ? (
                            <p className="mt-0.5 text-xs text-muted-foreground">
                              {permission.description}
                            </p>
                          ) : null}
                        </td>
                        {roleColumns.map((role) => {
                          const isOwner = role.role_key === OWNER_ROLE_KEY;
                          const checked = isOwner
                            ? true
                            : (draft[role.role_key] ?? []).includes(permission.permission);
                          const locked = !editing || isOwner || !canManage;
                          return (
                            <td
                              key={`${role.role_id}-${permission.permission}`}
                              className="text-center"
                            >
                              <input
                                type="checkbox"
                                aria-label={`${role.display_name || role.role_key} 的 ${permission.display_name}`}
                                className="h-4 w-4 cursor-pointer accent-primary disabled:cursor-not-allowed disabled:opacity-60"
                                checked={checked}
                                disabled={locked}
                                onChange={() =>
                                  togglePermission(role.role_key, permission.permission)
                                }
                              />
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <DialogFooter className="gap-2 pt-2">
          <Tooltip content="你没有调整角色权限的权限" disabled={canManage}>
            <span>
              <Button
                type="button"
                variant="outline"
                disabled={!canManage || loading || Boolean(loadError) || saveMutation.isPending}
                onClick={() => setEditing((prev) => !prev)}
              >
                <Pencil className="h-4 w-4" />
                {editing ? '取消编辑' : '编辑'}
              </Button>
            </span>
          </Tooltip>
          <Button
            type="button"
            disabled={
              !canManage || !editing || saveMutation.isPending || changedPayload.length === 0
            }
            onClick={() => saveMutation.mutate(changedPayload)}
          >
            {saveMutation.isPending ? '保存中…' : '保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
