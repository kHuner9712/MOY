import type React from "react";
'use client';

import { useCallback, useEffect, useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/feedback';
import { Field, Input } from '@/components/ui/input';
import { toast } from '@/components/ui/toast';
import { identityApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import type { OrganizationMember } from '@/lib/types';

/** 角色展示映射（业务语言） */
const ROLE_LABELS: Record<string, string> = {
  owner: '拥有者',
  org_admin: '管理员',
  member: '成员',
  guest: '访客',
};

/** 团队管理页：成员列表 + 邀请成员。 */
export default function TeamPage(): React.JSX.Element {
  const [orgId, setOrgId] = useState('');
  const [members, setMembers] = useState<OrganizationMember[]>([]);
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState('member');
  const [inviting, setInviting] = useState(false);

  /** 加载成员列表 */
  const load = useCallback(async (): Promise<void> => {
    if (!orgId) {
      return;
    }
    setLoading(true);
    setErrorMessage('');
    try {
      const page = await identityApi.listMembers(orgId, { page: 1, page_size: 50 });
      setMembers(page.items);
    } catch (error) {
      setErrorMessage(toBusinessMessage(error));
    } finally {
      setLoading(false);
    }
  }, [orgId]);

  useEffect(() => {
    void load();
  }, [load]);

  /** 邀请成员 */
  const handleInvite = async (): Promise<void> => {
    const email = inviteEmail.trim();
    if (!email) {
      toast.error('请填写成员邮箱');
      return;
    }
    setInviting(true);
    try {
      await identityApi.inviteMember(orgId, { email, role: inviteRole });
      toast.success('邀请已发送');
      setInviteOpen(false);
      setInviteEmail('');
      await load();
    } catch (error) {
      toast.error(toBusinessMessage(error));
    } finally {
      setInviting(false);
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>团队信息</CardTitle>
          <CardDescription>选择一个团队来查看成员与权限</CardDescription>
        </CardHeader>
        <CardContent className="flex items-end gap-2">
          <Field label="团队标识" htmlFor="org-id" className="flex-1">
            <Input
              id="org-id"
              value={orgId}
              onChange={(event) => setOrgId(event.target.value)}
              placeholder="例如：org_01K4Q8Z2X7M9B3N5P6R8T0V2W4"
            />
          </Field>
          <Dialog open={inviteOpen} onOpenChange={setInviteOpen}>
            <DialogTrigger asChild>
              <Button disabled={!orgId}>邀请成员</Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>邀请成员</DialogTitle>
                <DialogDescription>
                  邀请后对方将收到邮件，可在团队中协作
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-4">
                <Field label="成员邮箱" htmlFor="invite-email" required>
                  <Input
                    id="invite-email"
                    type="email"
                    value={inviteEmail}
                    onChange={(event) => setInviteEmail(event.target.value)}
                    placeholder="name@example.com"
                  />
                </Field>
                <Field label="角色" htmlFor="invite-role">
                  <select
                    id="invite-role"
                    value={inviteRole}
                    onChange={(event) => setInviteRole(event.target.value)}
                    className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                  >
                    <option value="member">成员</option>
                    <option value="org_admin">管理员</option>
                    <option value="guest">访客</option>
                  </select>
                </Field>
              </div>
              <DialogFooter>
                <Button
                  variant="outline"
                  onClick={() => setInviteOpen(false)}
                  disabled={inviting}
                >
                  取消
                </Button>
                <Button onClick={() => void handleInvite()} disabled={inviting}>
                  {inviting ? '发送中…' : '发送邀请'}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </CardContent>
      </Card>

      {orgId ? (
        <Card>
          <CardHeader>
            <CardTitle>成员列表</CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? (
              <LoadingState />
            ) : errorMessage ? (
              <ErrorState message={errorMessage} onRetry={() => void load()} />
            ) : members.length === 0 ? (
              <EmptyState title="暂无成员" description="邀请成员后即可在此处查看" />
            ) : (
              <ul className="divide-y">
                {members.map((member) => (
                  <li
                    key={member.user_id}
                    className="flex items-center justify-between py-3"
                  >
                    <div>
                      <p className="text-sm font-medium">{member.display_name}</p>
                      <p className="text-xs text-muted-foreground">{member.email}</p>
                    </div>
                    <Badge variant="secondary">
                      {ROLE_LABELS[member.role] ?? member.role}
                    </Badge>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
