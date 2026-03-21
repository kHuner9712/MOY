import {
  canAccessExecutive as canAccessExecutiveByOrgRole,
  canManageOrgCustomization as canManageOrgCustomizationByOrgRole,
  canManageTemplates as canManageTemplatesByOrgRole,
  canViewManagerWorkspace as canViewManagerWorkspaceByOrgRole,
  canViewOrgUsage as canViewOrgUsageByOrgRole,
  isOrgAdminRole
} from "@/lib/org-membership-utils";
import type { User, UserRole } from "@/types/auth";
import type { OrgMemberRole } from "@/types/productization";

export interface RoleCapabilityInput {
  role?: UserRole | null;
  orgRole?: OrgMemberRole | null;
}

const DISPLAY_ROLE_TO_ORG_ROLE: Record<UserRole, OrgMemberRole> = {
  manager: "manager",
  sales: "sales"
};

export function mapDisplayRoleToOrgRole(role: UserRole | null | undefined): OrgMemberRole | null {
  if (!role) return null;
  return DISPLAY_ROLE_TO_ORG_ROLE[role] ?? null;
}

export function resolveEffectiveOrgRole(input: RoleCapabilityInput | Pick<User, "role" | "orgRole"> | null | undefined): OrgMemberRole | null {
  if (!input) return null;
  if (input.orgRole) return input.orgRole;
  return mapDisplayRoleToOrgRole(input.role);
}

export function isOrgAdminLike(input: RoleCapabilityInput | Pick<User, "role" | "orgRole"> | null | undefined): boolean {
  return isOrgAdminRole(resolveEffectiveOrgRole(input));
}

export function canViewManagerWorkspace(input: RoleCapabilityInput | Pick<User, "role" | "orgRole"> | null | undefined): boolean {
  return canViewManagerWorkspaceByOrgRole(resolveEffectiveOrgRole(input));
}

export function canViewOrgUsage(input: RoleCapabilityInput | Pick<User, "role" | "orgRole"> | null | undefined): boolean {
  return canViewOrgUsageByOrgRole(resolveEffectiveOrgRole(input));
}

export function canAccessExecutive(input: RoleCapabilityInput | Pick<User, "role" | "orgRole"> | null | undefined): boolean {
  return canAccessExecutiveByOrgRole(resolveEffectiveOrgRole(input));
}

export function canManageTemplates(input: RoleCapabilityInput | Pick<User, "role" | "orgRole"> | null | undefined): boolean {
  return canManageTemplatesByOrgRole(resolveEffectiveOrgRole(input));
}

export function canManageOrgCustomization(input: RoleCapabilityInput | Pick<User, "role" | "orgRole"> | null | undefined): boolean {
  return canManageOrgCustomizationByOrgRole(resolveEffectiveOrgRole(input));
}
