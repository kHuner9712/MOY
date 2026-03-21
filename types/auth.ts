import type { OrgMemberRole, OrgSeatStatus } from "@/types/productization";

export type UserRole = "sales" | "manager";

export interface User {
  id: string;
  orgId: string;
  name: string;
  role: UserRole;
  title: string;
  email: string;
  team: string;
  orgRole?: OrgMemberRole;
  orgSeatStatus?: OrgSeatStatus;
  avatar?: string;
}
