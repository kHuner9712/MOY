'use client';

import { ArrowRight, Network, UserPlus, UserX } from 'lucide-react';
import Link from 'next/link';
import * as React from 'react';

import { StatusBadge } from '@/components/common/status-badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
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
import { Pagination } from '@/components/ui/pagination';
import { CompactSelect, Select } from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableEmpty,
  TableHead,
  TableHeader,
  TableLoading,
  TableRow,
} from '@/components/ui/table';
import { TabPanel, Tabs, useUrlTab } from '@/components/ui/tabs';
import { toast } from '@/components/ui/toast';
import { Tooltip } from '@/components/ui/tooltip';
import { QUERY_KEYS, useApiMutation, useApiQuery } from '@/hooks/use-api';
import { identityApi, identityExtApi, pluginsApi } from '@/lib/api-client';
import {
  PERSONAL_CAPABILITY_KEYS,
  capabilityLabel,
  isPersonalCapability,
} from '@/lib/capability-labels';
import { CapabilityList } from '@/components/plugin/capability-list';
import { toBusinessMessage } from '@/lib/business-errors';
import { formatDateTime, orPlaceholder } from '@/lib/format';
import type {
  CapabilityPolicyItem,
  CapabilityPolicyRequestItem,
  OrganizationMember,
} from '@/lib/types';

/** 角色展示映射（业务语言） */
const ROLE_LABELS: Readonly<Record<string, string>> = {
  owner: '所有者',
  org_admin: '企业管理员',
  member: '成员',
  guest: '访客',
};

/** 可选邀请角色 */
const INVITE_ROLE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'member', label: '成员' },
  { value: 'org_admin', label: '企业管理员' },
  { value: 'guest', label: '访客' },
];

/** 可管理成员的角色 */
const MANAGER_ROLES: readonly string[] = ['owner', 'org_admin'];

/** 操作记录结果筛选项 */
const RESULT_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: '全部结果' },
  { value: 'success', label: '成功' },
  { value: 'failed', label: '失败' },
];

/** 成员列表列数 */
const MEMBER_COLUMN_COUNT = 5;
/** 操作记录列数 */
const LOG_COLUMN_COUNT = 6;

/**
 * 团队管理页（P-04 深化）。
 *
 * 三个标签：成员 / 组织架构（跳转）/ 操作记录。
 * - 成员：角色下拉即时保存（ID8），移出成员（ID9）二次确认并说明数据处置；
 * - 组织架构：提供入口卡片跳转到 `/team/organization`；
 * - 操作记录：动作筛选项来自 ID11 目录，列表来自 U6。
 *
 * @returns 团队管理页元素
 */
export default function TeamPage(): React.JSX.Element {
  const [tab, setTab] = useUrlTab('tab', 'members');

  const profileQuery = useApiQuery(QUERY_KEYS.profile(), () => identityApi.profile());
  const organization = profileQuery.data?.organizations?.[0];
  const orgId = organization?.org_id ?? '';
  const canManage = organization ? MANAGER_ROLES.includes(organization.role) : false;

  // ---------------------------------------------------------------- 成员
  const [memberPage, setMemberPage] = React.useState<number>(1);
  const memberParams = { page: memberPage, page_size: 20 };
  const membersQuery = useApiQuery(
    QUERY_KEYS.members(orgId, memberParams),
    () => identityApi.listMembers(orgId, memberParams),
    { enabled: Boolean(orgId) },
  );

  const [inviteOpen, setInviteOpen] = React.useState<boolean>(false);
  const [inviteEmail, setInviteEmail] = React.useState<string>('');
  const [inviteRole, setInviteRole] = React.useState<string>('member');
  const [inviteError, setInviteError] = React.useState<string>('');
  const [removeTarget, setRemoveTarget] = React.useState<OrganizationMember | null>(null);

  const inviteMutation = useApiMutation(
    (variables: { email: string; role: string }) =>
      identityApi.inviteMember(orgId, { email: variables.email, role: variables.role }),
    {
      invalidateKeys: [QUERY_KEYS.members(orgId)],
      onSuccess: () => {
        toast.success('邀请已发送，对方接受后即可加入');
        setInviteOpen(false);
        setInviteEmail('');
        setInviteRole('member');
      },
      // 席位不足（15003）等错误直接用后端业务文案
      onError: (error) => setInviteError(toBusinessMessage(error)),
    },
  );

  const updateRoleMutation = useApiMutation(
    (variables: { memberId: string; role: string }) =>
      identityExtApi.updateMember(orgId, variables.memberId, { role: variables.role }),
    {
      invalidateKeys: [QUERY_KEYS.members(orgId)],
      onSuccess: () => toast.success('成员角色已更新'),
      onError: (error) => toast.error(toBusinessMessage(error)),
    },
  );

  const removeMutation = useApiMutation(
    (memberId: string) => identityExtApi.removeMember(orgId, memberId),
    {
      invalidateKeys: [QUERY_KEYS.members(orgId)],
      onSuccess: (data) => {
        toast.success(
          data.transferred_knowledge_base_count > 0
            ? `成员已移出，其 ${data.transferred_knowledge_base_count} 个知识库已转交企业管理员`
            : '成员已移出',
        );
        setRemoveTarget(null);
      },
      onError: (error) => toast.error(toBusinessMessage(error)),
    },
  );

  const handleInvite = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const email = inviteEmail.trim();
    if (!email) {
      setInviteError('请填写成员邮箱');
      return;
    }
    setInviteError('');
    inviteMutation.mutate({ email, role: inviteRole });
  };

  // ------------------------------------------------------------ 操作记录
  const [logPage, setLogPage] = React.useState<number>(1);
  const [action, setAction] = React.useState<string>('');
  const [result, setResult] = React.useState<string>('');

  const actionsQuery = useApiQuery(
    QUERY_KEYS.auditActions(),
    () => identityExtApi.listAuditActions(),
    { enabled: tab === 'logs' },
  );

  const logBody = {
    page: logPage,
    page_size: 20,
    ...(action ? { action } : {}),
    ...(result ? { result: result as 'success' | 'failed' } : {}),
  };
  const logsQuery = useApiQuery(
    QUERY_KEYS.auditLogs(logBody),
    () => identityApi.auditLogs(logBody),
    { enabled: tab === 'logs' && Boolean(orgId) },
  );

  const actionOptions = React.useMemo<Array<{ value: string; label: string }>>(() => {
    const groups = actionsQuery.data?.groups ?? [];
    const options: Array<{ value: string; label: string }> = [{ value: '', label: '全部动作' }];
    for (const group of groups) {
      for (const item of group.actions) {
        options.push({ value: item.action, label: `${group.group_name} · ${item.display_name}` });
      }
    }
    return options;
  }, [actionsQuery.data]);

  // ---------------------------------------------------------------- 三态
  if (profileQuery.isLoading) {
    return <LoadingState label="正在加载团队信息…" />;
  }

  if (profileQuery.error) {
    return (
      <ErrorState
        message={toBusinessMessage(profileQuery.error)}
        onRetry={() => void profileQuery.refetch()}
      />
    );
  }

  if (!organization) {
    return (
      <EmptyState
        title="还没有加入任何企业"
        description="团队管理需要企业空间；请联系管理员确认你的成员身份后刷新页面"
      />
    );
  }

  const members = membersQuery.data?.items ?? [];
  const memberTotal = membersQuery.data?.meta.total ?? 0;
  const logs = logsQuery.data?.items ?? [];
  const logTotal = logsQuery.data?.meta.total ?? 0;

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <Tabs
        items={[
          { value: 'members', label: '成员', count: memberTotal },
          { value: 'structure', label: '组织架构' },
          { value: 'capabilities', label: '插件能力' },
          { value: 'logs', label: '操作记录' },
        ]}
        value={tab}
        onValueChange={setTab}
        ariaLabel="团队管理视图切换"
      />

      <TabPanel value="members" activeValue={tab}>
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between gap-2">
              <CardTitle>成员</CardTitle>
              <Tooltip content="只有所有者与企业管理员可以邀请成员" disabled={canManage}>
                <span>
                  <Button
                    type="button"
                    size="sm"
                    disabled={!canManage}
                    onClick={() => setInviteOpen(true)}
                  >
                    <UserPlus className="h-4 w-4" />
                    邀请成员
                  </Button>
                </span>
              </Tooltip>
            </div>
            <CardDescription>角色变更即时生效；移出成员前请确认其知识库归属</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>成员</TableHead>
                  <TableHead>邮箱</TableHead>
                  <TableHead>角色</TableHead>
                  <TableHead>加入时间</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {membersQuery.isLoading ? (
                  <TableLoading columns={MEMBER_COLUMN_COUNT} rows={4} />
                ) : membersQuery.error ? (
                  <TableRow className="hover:bg-transparent">
                    <TableCell colSpan={MEMBER_COLUMN_COUNT} className="p-0">
                      <ErrorState
                        message={toBusinessMessage(membersQuery.error)}
                        onRetry={() => void membersQuery.refetch()}
                      />
                    </TableCell>
                  </TableRow>
                ) : members.length === 0 ? (
                  <TableEmpty
                    colSpan={MEMBER_COLUMN_COUNT}
                    title="还没有其他成员"
                    description="邀请同事加入后，就可以一起维护知识库与共享问答"
                  />
                ) : (
                  members.map((member) => {
                    const locked = !canManage || member.role === 'owner';
                    return (
                      <TableRow key={member.user_id}>
                        <TableCell className="text-sm font-medium">
                          {orPlaceholder(member.display_name)}
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground">
                          {orPlaceholder(member.email)}
                        </TableCell>
                        <TableCell>
                          <div className="w-32">
                            <CompactSelect
                              aria-label={`调整 ${member.display_name} 的角色`}
                              value={member.role}
                              disabled={locked}
                              onChange={(event) =>
                                updateRoleMutation.mutate({
                                  memberId: member.user_id,
                                  role: event.target.value,
                                })
                              }
                              options={Object.entries(ROLE_LABELS).map(([value, label]) => ({
                                value,
                                label,
                              }))}
                            />
                          </div>
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                          {formatDateTime(member.joined_at)}
                        </TableCell>
                        <TableCell className="text-right">
                          <Tooltip
                            content={
                              member.role === 'owner'
                                ? '不能移除企业所有者，请先转移所有权'
                                : '只有所有者与企业管理员可以移出成员'
                            }
                            disabled={!locked}
                          >
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              disabled={locked}
                              onClick={() => setRemoveTarget(member)}
                            >
                              <UserX className="h-4 w-4" />
                              移出
                            </Button>
                          </Tooltip>
                        </TableCell>
                      </TableRow>
                    );
                  })
                )}
              </TableBody>
            </Table>

            {memberTotal > 20 ? (
              <Pagination
                page={memberPage}
                pageSize={20}
                total={memberTotal}
                onPageChange={setMemberPage}
              />
            ) : null}
          </CardContent>
        </Card>
      </TabPanel>

      <TabPanel value="structure" activeValue={tab}>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Network aria-hidden="true" className="h-4 w-4 text-primary" />
              组织架构
            </CardTitle>
            <CardDescription>
              维护部门层级（最多 5 层）与角色权限矩阵，成员的数据范围按部门划分
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button asChild>
              <Link href="/team/organization">
                进入组织架构
                <ArrowRight className="h-4 w-4" />
              </Link>
            </Button>
          </CardContent>
        </Card>
      </TabPanel>

      <TabPanel value="capabilities" activeValue={tab}>
        <OrgCapabilityPanel canManage={canManage} />
      </TabPanel>

      <TabPanel value="logs" activeValue={tab}>
        <Card>
          <CardHeader>
            <CardTitle>操作记录</CardTitle>
            <CardDescription>
              记录成员、知识库、订单等关键操作；查询时间跨度最长 90 天
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-col gap-2 sm:flex-row">
              <div className="sm:w-64">
                <CompactSelect
                  aria-label="按动作筛选"
                  value={action}
                  onChange={(event) => {
                    setAction(event.target.value);
                    setLogPage(1);
                  }}
                  options={actionOptions}
                />
              </div>
              <div className="sm:w-36">
                <CompactSelect
                  aria-label="按结果筛选"
                  value={result}
                  onChange={(event) => {
                    setResult(event.target.value);
                    setLogPage(1);
                  }}
                  options={RESULT_OPTIONS}
                />
              </div>
            </div>

            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>动作</TableHead>
                  <TableHead>操作人</TableHead>
                  <TableHead>结果</TableHead>
                  <TableHead>变更摘要</TableHead>
                  <TableHead>来源</TableHead>
                  <TableHead>时间</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {logsQuery.isLoading ? (
                  <TableLoading columns={LOG_COLUMN_COUNT} rows={4} />
                ) : logsQuery.error ? (
                  <TableRow className="hover:bg-transparent">
                    <TableCell colSpan={LOG_COLUMN_COUNT} className="p-0">
                      <ErrorState
                        message={toBusinessMessage(logsQuery.error)}
                        onRetry={() => void logsQuery.refetch()}
                      />
                    </TableCell>
                  </TableRow>
                ) : logs.length === 0 ? (
                  <TableEmpty
                    colSpan={LOG_COLUMN_COUNT}
                    title={action || result ? '没有符合条件的记录' : '还没有操作记录'}
                    description={
                      action || result
                        ? '换个动作或结果再看，也可以清空筛选查看全部记录'
                        : '成员管理、知识库与订单等关键操作都会留下记录'
                    }
                  />
                ) : (
                  logs.map((log) => (
                    <TableRow key={log.log_id}>
                      <TableCell className="text-sm">
                        {orPlaceholder(log.action_display_name, log.action)}
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {orPlaceholder(log.actor_name)}
                        {log.actor_role
                          ? `（${ROLE_LABELS[log.actor_role] ?? log.actor_role}）`
                          : ''}
                      </TableCell>
                      <TableCell>
                        <StatusBadge
                          status={log.result ?? 'success'}
                          label={log.result === 'failed' ? '未成功' : '成功'}
                        />
                      </TableCell>
                      <TableCell className="max-w-[18rem] truncate text-sm text-muted-foreground">
                        {orPlaceholder(log.change_summary)}
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {orPlaceholder(log.ip_masked)}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                        {formatDateTime(log.created_at)}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>

            {logTotal > 20 ? (
              <Pagination page={logPage} pageSize={20} total={logTotal} onPageChange={setLogPage} />
            ) : null}
          </CardContent>
        </Card>
      </TabPanel>

      <Dialog open={inviteOpen} onOpenChange={inviteMutation.isPending ? undefined : setInviteOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>邀请成员</DialogTitle>
            <DialogDescription>
              对方接受邀请后即可加入企业，并按所选角色获得对应权限。
            </DialogDescription>
          </DialogHeader>
          <form className="space-y-4" onSubmit={handleInvite}>
            <Field label="成员邮箱" htmlFor="invite-email" error={inviteError} required>
              <Input
                id="invite-email"
                type="email"
                value={inviteEmail}
                invalid={Boolean(inviteError)}
                onChange={(event) => setInviteEmail(event.target.value)}
                placeholder="name@example.com"
                autoFocus
              />
            </Field>
            <Field label="角色" htmlFor="invite-role">
              <Select
                id="invite-role"
                value={inviteRole}
                onChange={(event) => setInviteRole(event.target.value)}
                options={INVITE_ROLE_OPTIONS}
              />
            </Field>
            <DialogFooter className="gap-2 pt-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => setInviteOpen(false)}
                disabled={inviteMutation.isPending}
              >
                取消
              </Button>
              <Button type="submit" disabled={inviteMutation.isPending}>
                {inviteMutation.isPending ? '发送中…' : '发送邀请'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={removeTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRemoveTarget(null);
        }}
        variant="danger"
        title="移出该成员？"
        description="移出后该成员将无法访问企业资料；其创建的知识库会自动转交企业管理员。"
        confirmLabel="移出成员"
        loading={removeMutation.isPending}
        onConfirm={async () => {
          if (!removeTarget) return;
          await removeMutation.mutateAsync(removeTarget.user_id);
        }}
      >
        {removeTarget ? (
          <p>
            {orPlaceholder(removeTarget.display_name)}（{orPlaceholder(removeTarget.email)}）
          </p>
        ) : null}
      </ConfirmDialog>
    </div>
  );
}

/**
 * 企业能力开关面板（PL13 写 / PL1 顶层读）。
 *
 * 读回路：PL1 市场响应顶层新增 `org_capabilities`（只含 3 个 org.*），
 * 因此**没有安装任何插件时也能读**——这是本面板与「探针式读取」的关键差别。
 * 保存后以 PL13 返回的全量 6 项为准（含 personal.* 恒开启项与修改人/时间）。
 *
 * @param props - 组件属性
 * @returns 面板元素
 */
function OrgCapabilityPanel({ canManage }: { canManage: boolean }): React.JSX.Element {
  const marketQuery = useApiQuery(QUERY_KEYS.plugins({ page: 1, page_size: 1 }), () =>
    pluginsApi.market({ page: 1, page_size: 1 }),
  );

  const [draft, setDraft] = React.useState<Record<string, boolean>>({});
  const [saved, setSaved] = React.useState<CapabilityPolicyItem[]>([]);

  /** 待渲染的 6 项：保存前用「个人 3 项恒开 + PL1 的企业 3 项」，保存后用 PL13 全量 */
  const rows: CapabilityPolicyItem[] = React.useMemo(() => {
    if (saved.length > 0) return saved;
    const personalRows: CapabilityPolicyItem[] = PERSONAL_CAPABILITY_KEYS.map((key) => ({
      capability_key: key,
      display_name: '',
      org_switch_enabled: true,
      updated_by_name: '',
      updated_at: '',
    }));
    const orgRows: CapabilityPolicyItem[] = (marketQuery.data?.org_capabilities ?? []).map(
      (item) => ({
        capability_key: item.capability_key,
        display_name: '',
        org_switch_enabled: item.org_switch_enabled,
        updated_by_name: '',
        updated_at: '',
      }),
    );
    return [...personalRows, ...orgRows];
  }, [saved, marketQuery.data]);

  React.useEffect(() => {
    if (rows.length === 0) return;
    setDraft((prev) => {
      const next: Record<string, boolean> = { ...prev };
      for (const row of rows) {
        if (next[row.capability_key] === undefined) {
          next[row.capability_key] = row.org_switch_enabled;
        }
      }
      return next;
    });
  }, [rows]);

  const saveMutation = useApiMutation(
    (policies: CapabilityPolicyRequestItem[]) => pluginsApi.setCapabilityPolicy({ policies }),
    {
      invalidateKeys: [['plugins']],
      onSuccess: (data) => {
        setSaved(data.policies);
        toast.success('企业能力开关已保存');
      },
      onError: (error) => toast.error(toBusinessMessage(error)),
    },
  );

  /** 只提交企业能力：个人能力恒开，传 personal.* 会被后端判参数错 */
  const handleSave = (): void => {
    const policies = rows
      .filter((row) => !isPersonalCapability(row.capability_key))
      .map((row) => ({
        capability_key: row.capability_key,
        org_switch_enabled: Boolean(draft[row.capability_key]),
      }));
    if (policies.length === 0) {
      toast.error('还没有可配置的企业能力');
      return;
    }
    saveMutation.mutate(policies);
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>企业能力开关</CardTitle>
        <CardDescription>
          个人能力（读取你本人的资料）恒开启；企业能力由管理员统一决定是否对全员开放。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {marketQuery.isLoading ? (
          <LoadingState label="正在加载能力开关…" />
        ) : marketQuery.error ? (
          <ErrorState
            message={toBusinessMessage(marketQuery.error)}
            onRetry={() => void marketQuery.refetch()}
          />
        ) : (
          <>
            <ul className="space-y-2">
              {rows.map((row) => {
                const personal = isPersonalCapability(row.capability_key);
                const checked = personal ? true : Boolean(draft[row.capability_key]);
                return (
                  <li
                    key={row.capability_key}
                    className="flex items-start justify-between gap-3 rounded-md border p-3"
                  >
                    <div className="min-w-0">
                      <p className="text-sm font-medium">
                        {capabilityLabel(row.capability_key, row.display_name)}
                      </p>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        {personal ? '个人能力，始终开启' : '企业能力，关闭后全员都无法授权使用'}
                      </p>
                      {row.updated_by_name ? (
                        <p className="mt-0.5 text-xs text-muted-foreground">
                          最近修改：{row.updated_by_name} · {formatDateTime(row.updated_at)}
                        </p>
                      ) : null}
                    </div>
                    <label className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
                      <input
                        type="checkbox"
                        className="h-4 w-4 cursor-pointer accent-primary disabled:cursor-not-allowed disabled:opacity-60"
                        aria-label={`${capabilityLabel(row.capability_key, row.display_name)}开关`}
                        checked={checked}
                        disabled={personal || !canManage || saveMutation.isPending}
                        onChange={(event) =>
                          setDraft((prev) => ({
                            ...prev,
                            [row.capability_key]: event.target.checked,
                          }))
                        }
                      />
                      {personal ? '恒开启' : checked ? '已开启' : '已关闭'}
                    </label>
                  </li>
                );
              })}
            </ul>
            <div className="flex flex-wrap items-center gap-2">
              <Tooltip content="只有所有者与企业管理员可以调整企业能力" disabled={canManage}>
                <span>
                  <Button
                    type="button"
                    size="sm"
                    disabled={!canManage || saveMutation.isPending}
                    onClick={handleSave}
                  >
                    {saveMutation.isPending ? '保存中…' : '保存开关'}
                  </Button>
                </span>
              </Tooltip>
              <span className="text-xs text-muted-foreground">
                关闭后，插件申请该能力时会提示「该能力尚未对企业开放」
              </span>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
