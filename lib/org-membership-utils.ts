import type { OrgMemberRole, OrgSeatStatus } from "@/types/productization";

export const ORG_ADMIN_ROLES: OrgMemberRole[] = ["owner", "admin"];
export const ORG_MANAGER_ROLES: OrgMemberRole[] = ["owner", "admin", "manager"];

export function isOrgAdminRole(role: OrgMemberRole | null | undefined): boolean {
  return !!role && ORG_ADMIN_ROLES.includes(role);
}

export function canViewOrgUsage(role: OrgMemberRole | null | undefined): boolean {
  return !!role && ORG_MANAGER_ROLES.includes(role);
}

export function isSeatStatusTransitionAllowed(from: OrgSeatStatus, to: OrgSeatStatus): boolean {
  if (from === to) return true;

  const allowed: Record<OrgSeatStatus, OrgSeatStatus[]> = {
    invited: ["active", "suspended", "removed"],
    active: ["suspended", "removed"],
    suspended: ["active", "removed"],
    removed: []
  };

  return allowed[from].includes(to);
}
