export type UserRole = "sales" | "manager";

export interface User {
  id: string;
  orgId: string;
  name: string;
  role: UserRole;
  title: string;
  email: string;
  team: string;
  avatar?: string;
}
