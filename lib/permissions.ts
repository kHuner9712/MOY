import type { Database } from "@/types/database";

type ProfileRow = Database["public"]["Tables"]["profiles"]["Row"];
type CustomerRow = Database["public"]["Tables"]["customers"]["Row"];
type AlertRow = Database["public"]["Tables"]["alerts"]["Row"];

export function isManager(profile: Pick<ProfileRow, "role"> | null | undefined): boolean {
  return profile?.role === "manager";
}

export function canAccessCustomer(profile: ProfileRow, customer: Pick<CustomerRow, "owner_id" | "org_id">): boolean {
  if (profile.org_id !== customer.org_id) return false;
  return isManager(profile) || customer.owner_id === profile.id;
}

export function canOperateAlert(profile: ProfileRow, alert: Pick<AlertRow, "org_id" | "owner_id">): boolean {
  if (profile.org_id !== alert.org_id) return false;
  return isManager(profile) || (alert.owner_id !== null && alert.owner_id === profile.id);
}
