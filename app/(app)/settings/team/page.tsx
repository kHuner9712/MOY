"use client";

import { useEffect, useMemo, useState } from "react";
import { MailPlus, RefreshCw } from "lucide-react";

import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { settingsClientService } from "@/services/settings-client-service";
import type { OrgInvite, OrgMembership, OrgMemberRole, OrgSeatStatus } from "@/types/productization";

interface TeamState {
  role: string;
  canManageTeam: boolean;
  members: OrgMembership[];
  invites: OrgInvite[];
}

const ROLE_OPTIONS: OrgMemberRole[] = ["owner", "admin", "manager", "sales", "viewer"];
const SEAT_OPTIONS: OrgSeatStatus[] = ["active", "invited", "suspended", "removed"];

export default function TeamSettingsPage(): JSX.Element {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<OrgMemberRole>("sales");
  const [latestInviteLink, setLatestInviteLink] = useState<string | null>(null);
  const [state, setState] = useState<TeamState | null>(null);

  const canManage = state?.canManageTeam ?? false;

  const activeCount = useMemo(() => state?.members.filter((item) => item.seatStatus === "active").length ?? 0, [state]);

  async function load(): Promise<void> {
    try {
      setLoading(true);
      setError(null);
      const payload = await settingsClientService.getTeamSettings();
      setState({
        role: payload.role,
        canManageTeam: payload.canManageTeam,
        members: payload.members,
        invites: payload.invites
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load team settings");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function handleInvite(): Promise<void> {
    if (!inviteEmail.trim()) return;

    setSaving(true);
    setError(null);
    try {
      const payload = await settingsClientService.inviteMember({
        email: inviteEmail.trim(),
        intendedRole: inviteRole
      });
      setLatestInviteLink(payload.inviteLink);
      setInviteEmail("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create invite");
    } finally {
      setSaving(false);
    }
  }

  async function handleRoleUpdate(membershipId: string, role: OrgMemberRole): Promise<void> {
    setSaving(true);
    setError(null);
    try {
      await settingsClientService.updateMemberRole({ membershipId, role });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update role");
    } finally {
      setSaving(false);
    }
  }

  async function handleSeatUpdate(membershipId: string, seatStatus: OrgSeatStatus): Promise<void> {
    setSaving(true);
    setError(null);
    try {
      await settingsClientService.updateMemberSeatStatus({ membershipId, seatStatus });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update seat status");
    } finally {
      setSaving(false);
    }
  }

  if (loading || !state) {
    return <div className="text-sm text-muted-foreground">Loading team settings...</div>;
  }

  return (
    <div>
      <PageHeader
        title="Team & Seats"
        description="Invite members, assign roles, and control seat activation status."
        action={
          <Button variant="outline" onClick={() => void load()} disabled={loading || saving}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
        }
      />

      {error ? <p className="mb-4 text-sm text-red-600">{error}</p> : null}

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>Members ({activeCount} active)</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {state.members.map((member) => (
              <div key={member.id} className="rounded-md border border-slate-200 p-3">
                <div className="mb-2 flex items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium text-slate-900">{member.userName ?? member.userId}</p>
                    <p className="text-xs text-muted-foreground">{member.userTitle ?? "-"}</p>
                  </div>
                  <div className="text-xs text-muted-foreground">{member.lastActiveAt ? `last active ${member.lastActiveAt.slice(0, 10)}` : "no activity yet"}</div>
                </div>

                <div className="grid gap-2 sm:grid-cols-2">
                  <div>
                    <Label className="mb-1 block text-xs">Role</Label>
                    <select
                      className="h-9 w-full rounded-md border border-slate-200 bg-white px-2 text-sm"
                      value={member.role}
                      disabled={!canManage || saving}
                      onChange={(event) => void handleRoleUpdate(member.id, event.target.value as OrgMemberRole)}
                    >
                      {ROLE_OPTIONS.map((option) => (
                        <option key={option} value={option}>
                          {option}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <Label className="mb-1 block text-xs">Seat Status</Label>
                    <select
                      className="h-9 w-full rounded-md border border-slate-200 bg-white px-2 text-sm"
                      value={member.seatStatus}
                      disabled={!canManage || saving}
                      onChange={(event) => void handleSeatUpdate(member.id, event.target.value as OrgSeatStatus)}
                    >
                      {SEAT_OPTIONS.map((option) => (
                        <option key={option} value={option}>
                          {option}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Invite Member</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="space-y-2">
              <Label>Email</Label>
              <Input
                value={inviteEmail}
                onChange={(event) => setInviteEmail(event.target.value)}
                placeholder="teammate@company.com"
                disabled={!canManage || saving}
              />
            </div>
            <div className="space-y-2">
              <Label>Intended Role</Label>
              <select
                className="h-9 w-full rounded-md border border-slate-200 bg-white px-2 text-sm"
                value={inviteRole}
                disabled={!canManage || saving}
                onChange={(event) => setInviteRole(event.target.value as OrgMemberRole)}
              >
                {ROLE_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </div>
            <Button className="w-full" disabled={!canManage || saving || !inviteEmail.trim()} onClick={() => void handleInvite()}>
              <MailPlus className="mr-2 h-4 w-4" />
              Send Invite (token)
            </Button>
            {!canManage ? <p className="text-xs text-muted-foreground">Only owner/admin can manage invites and seats.</p> : null}
            {latestInviteLink ? (
              <div className="rounded-md border border-sky-200 bg-sky-50 p-2 text-xs text-sky-900">
                Invite link: <span className="break-all">{latestInviteLink}</span>
              </div>
            ) : null}
          </CardContent>
        </Card>

        <Card className="xl:col-span-3">
          <CardHeader>
            <CardTitle>Recent Invites</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {state.invites.length === 0 ? <p className="text-muted-foreground">No invite records.</p> : null}
            {state.invites.map((invite) => (
              <div key={invite.id} className="rounded-md border border-slate-200 p-2">
                <p className="font-medium text-slate-900">{invite.email}</p>
                <p className="text-xs text-muted-foreground">
                  role={invite.intendedRole} · status={invite.inviteStatus} · expires={invite.expiresAt.slice(0, 10)}
                </p>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

