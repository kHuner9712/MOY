import { randomUUID } from "crypto";

import {
  canAccessExecutive,
  canManageOrgCustomization,
  canManageTemplates,
  canViewManagerWorkspace,
  ORG_ADMIN_ROLES,
  ORG_MANAGER_ROLES,
  canViewOrgUsage,
  isOrgAdminRole,
  isSeatStatusTransitionAllowed
} from "@/lib/org-membership-utils";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import {
  mapOrgInviteRow,
  mapOrgMembershipRow
} from "@/services/mappers";
import type { Database } from "@/types/database";
import type { OrgInvite, OrgMemberRole, OrgMembership, OrgSeatStatus } from "@/types/productization";

type DbClient = ServerSupabaseClient;
type MembershipRow = Database["public"]["Tables"]["org_memberships"]["Row"];
type InviteRow = Database["public"]["Tables"]["org_invites"]["Row"];
type ProfileRow = Database["public"]["Tables"]["profiles"]["Row"];

export {
  ORG_ADMIN_ROLES,
  ORG_MANAGER_ROLES,
  canAccessExecutive,
  canManageOrgCustomization,
  canManageTemplates,
  canViewManagerWorkspace,
  canViewOrgUsage,
  isOrgAdminRole,
  isSeatStatusTransitionAllowed
};

function mapProfileRoleToMembershipRole(profileRole: ProfileRow["role"]): OrgMemberRole {
  return profileRole === "manager" ? "manager" : "sales";
}

function mapMembershipRoleToProfileRole(membershipRole: OrgMemberRole): ProfileRow["role"] {
  return membershipRole === "owner" || membershipRole === "admin" || membershipRole === "manager" ? "manager" : "sales";
}

export async function getCurrentOrgMembership(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
}): Promise<OrgMembership | null> {
  const membershipRes = await params.supabase
    .from("org_memberships")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("user_id", params.userId)
    .maybeSingle();

  if (membershipRes.error) throw new Error(membershipRes.error.message);

  const membership = (membershipRes.data ?? null) as MembershipRow | null;
  if (membership) {
    return mapOrgMembershipRow(membership);
  }

  const profileRes = await params.supabase
    .from("profiles")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("id", params.userId)
    .maybeSingle();

  if (profileRes.error) throw new Error(profileRes.error.message);
  const profile = (profileRes.data ?? null) as ProfileRow | null;
  if (!profile || !profile.is_active) return null;

  return {
    id: "fallback",
    orgId: params.orgId,
    userId: params.userId,
    role: mapProfileRoleToMembershipRole(profile.role),
    seatStatus: "active",
    invitedBy: null,
    invitedAt: profile.created_at,
    joinedAt: profile.created_at,
    lastActiveAt: null,
    createdAt: profile.created_at,
    updatedAt: profile.updated_at,
    userName: profile.display_name,
    userTitle: profile.title ?? undefined,
    profileRole: profile.role
  };
}

export async function assertOrgAdminAccess(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
}): Promise<OrgMembership> {
  const membership = await getCurrentOrgMembership(params);
  if (!membership || membership.seatStatus !== "active" || !isOrgAdminRole(membership.role)) {
    throw new Error("org_admin_access_required");
  }
  return membership;
}

export async function assertOrgManagerAccess(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
}): Promise<OrgMembership> {
  const membership = await getCurrentOrgMembership(params);
  if (!membership || membership.seatStatus !== "active" || !canViewOrgUsage(membership.role)) {
    throw new Error("org_manager_access_required");
  }
  return membership;
}

export async function listOrgMembers(params: {
  supabase: DbClient;
  orgId: string;
}): Promise<OrgMembership[]> {
  const membershipRes = await params.supabase
    .from("org_memberships")
    .select("*")
    .eq("org_id", params.orgId)
    .order("created_at", { ascending: true });
  if (membershipRes.error) throw new Error(membershipRes.error.message);

  const memberships = (membershipRes.data ?? []) as MembershipRow[];
  if (memberships.length === 0) return [];

  const userIds = Array.from(new Set(memberships.map((item) => item.user_id)));
  const profileRes = await params.supabase
    .from("profiles")
    .select("*")
    .eq("org_id", params.orgId)
    .in("id", userIds);
  if (profileRes.error) throw new Error(profileRes.error.message);

  const profiles = (profileRes.data ?? []) as ProfileRow[];
  const profileMap = new Map<string, ProfileRow>();
  for (const profile of profiles) {
    profileMap.set(profile.id, profile);
  }

  return memberships.map((row) => {
    const profile = profileMap.get(row.user_id) ?? null;
    const mapped = mapOrgMembershipRow({
      ...row,
      profile
    });
    return {
      ...mapped,
      userEmail: ""
    };
  });
}

export async function updateMembershipRole(params: {
  supabase: DbClient;
  orgId: string;
  membershipId: string;
  role: OrgMemberRole;
}): Promise<OrgMembership> {
  const currentRes = await params.supabase
    .from("org_memberships")
    .select("id,user_id")
    .eq("org_id", params.orgId)
    .eq("id", params.membershipId)
    .single();
  if (currentRes.error) throw new Error(currentRes.error.message);
  const current = currentRes.data as Pick<MembershipRow, "id" | "user_id">;

  const updateRes = await params.supabase
    .from("org_memberships")
    .update({
      role: params.role
    })
    .eq("org_id", params.orgId)
    .eq("id", params.membershipId)
    .select("*")
    .single();

  if (updateRes.error) throw new Error(updateRes.error.message);

  const profileUpdateRes = await params.supabase
    .from("profiles")
    .update({
      role: mapMembershipRoleToProfileRole(params.role)
    })
    .eq("org_id", params.orgId)
    .eq("id", current.user_id);
  if (profileUpdateRes.error) throw new Error(profileUpdateRes.error.message);

  return mapOrgMembershipRow(updateRes.data as MembershipRow);
}

export async function updateMembershipSeatStatus(params: {
  supabase: DbClient;
  orgId: string;
  membershipId: string;
  seatStatus: OrgSeatStatus;
}): Promise<OrgMembership> {
  const currentRes = await params.supabase
    .from("org_memberships")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("id", params.membershipId)
    .single();
  if (currentRes.error) throw new Error(currentRes.error.message);
  const current = currentRes.data as MembershipRow;

  if (!isSeatStatusTransitionAllowed(current.seat_status, params.seatStatus)) {
    throw new Error("seat_status_transition_not_allowed");
  }

  const updateRes = await params.supabase
    .from("org_memberships")
    .update({
      seat_status: params.seatStatus
    })
    .eq("org_id", params.orgId)
    .eq("id", params.membershipId)
    .select("*")
    .single();

  if (updateRes.error) throw new Error(updateRes.error.message);
  return mapOrgMembershipRow(updateRes.data as MembershipRow);
}

export async function listOrgInvites(params: {
  supabase: DbClient;
  orgId: string;
  includeExpired?: boolean;
}): Promise<OrgInvite[]> {
  let query = params.supabase
    .from("org_invites")
    .select("*")
    .eq("org_id", params.orgId)
    .order("created_at", { ascending: false });

  if (!params.includeExpired) {
    query = query.not("invite_status", "eq", "expired");
  }

  const res = await query;
  if (res.error) throw new Error(res.error.message);

  return ((res.data ?? []) as InviteRow[]).map((row) => mapOrgInviteRow(row));
}

export async function createOrgInvite(params: {
  supabase: DbClient;
  orgId: string;
  email: string;
  intendedRole: OrgMemberRole;
  invitedBy: string;
  expiresAt?: string;
}): Promise<{ invite: OrgInvite; inviteLink: string }> {
  const inviteToken = randomUUID();
  const expiresAt = params.expiresAt ?? new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString();

  const insertRes = await params.supabase
    .from("org_invites")
    .insert({
      org_id: params.orgId,
      email: params.email.trim().toLowerCase(),
      intended_role: params.intendedRole,
      invite_status: "pending",
      invite_token: inviteToken,
      invited_by: params.invitedBy,
      expires_at: expiresAt
    })
    .select("*")
    .single();

  if (insertRes.error) throw new Error(insertRes.error.message);

  const invite = mapOrgInviteRow(insertRes.data as InviteRow);
  const baseUrl = process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000";
  const inviteLink = `${baseUrl.replace(/\/$/, "")}/invite/${inviteToken}`;

  return {
    invite,
    inviteLink
  };
}

export async function markInviteStatus(params: {
  supabase: DbClient;
  orgId: string;
  inviteId: string;
  status: OrgInvite["inviteStatus"];
}): Promise<OrgInvite> {
  const updateRes = await params.supabase
    .from("org_invites")
    .update({
      invite_status: params.status
    })
    .eq("org_id", params.orgId)
    .eq("id", params.inviteId)
    .select("*")
    .single();

  if (updateRes.error) throw new Error(updateRes.error.message);
  return mapOrgInviteRow(updateRes.data as InviteRow);
}
