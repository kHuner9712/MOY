import type { AuthChangeEvent, Session } from "@supabase/supabase-js";

import { createSupabaseBrowserClient } from "@/lib/supabase/client";
import { mapProfileToUser } from "@/services/mappers";
import type { User, UserRole } from "@/types/auth";
import type { Database } from "@/types/database";

interface DemoAccount {
  label: string;
  email: string;
  role: UserRole;
}

type ProfileRow = Database["public"]["Tables"]["profiles"]["Row"];
type MembershipRow = Database["public"]["Tables"]["org_memberships"]["Row"];

function mapMembershipRoleToUserRole(role: MembershipRow["role"]): UserRole {
  return role === "owner" || role === "admin" || role === "manager" ? "manager" : "sales";
}

function getDemoAccounts(): DemoAccount[] {
  return [
    {
      label: "管理者（Demo）",
      email: process.env.NEXT_PUBLIC_DEMO_MANAGER_EMAIL ?? "manager@demo.moy",
      role: "manager"
    },
    {
      label: "销售 林悦（Demo）",
      email: process.env.NEXT_PUBLIC_DEMO_SALES_1_EMAIL ?? "linyue@demo.moy",
      role: "sales"
    },
    {
      label: "销售 陈航（Demo）",
      email: process.env.NEXT_PUBLIC_DEMO_SALES_2_EMAIL ?? "chenhang@demo.moy",
      role: "sales"
    },
    {
      label: "销售 吴凡（Demo）",
      email: process.env.NEXT_PUBLIC_DEMO_SALES_3_EMAIL ?? "wufan@demo.moy",
      role: "sales"
    }
  ];
}

async function fetchProfileById(userId: string, email: string | undefined): Promise<User | null> {
  const supabase = createSupabaseBrowserClient();
  const { data, error } = await supabase.from("profiles").select("*").eq("id", userId).maybeSingle();
  const profile = (data ?? null) as ProfileRow | null;
  if (error || !profile || !profile.is_active) return null;

  const membershipRes = await supabase
    .from("org_memberships")
    .select("role, seat_status")
    .eq("org_id", profile.org_id)
    .eq("user_id", userId)
    .maybeSingle();
  if (membershipRes.error) return null;

  const membership = (membershipRes.data ?? null) as Pick<MembershipRow, "role" | "seat_status"> | null;
  if (membership && membership.seat_status !== "active") return null;
  const effectiveRole = membership ? mapMembershipRoleToUserRole(membership.role) : undefined;

  return mapProfileToUser(profile, email, effectiveRole);
}

export const authService = {
  isDemoAuthEnabled(): boolean {
    return process.env.NEXT_PUBLIC_ENABLE_DEMO_AUTH === "true";
  },

  getDemoAccounts(): DemoAccount[] {
    if (process.env.NEXT_PUBLIC_ENABLE_DEMO_AUTH !== "true") return [];
    return getDemoAccounts();
  },

  getDemoDefaultPassword(): string {
    return process.env.NEXT_PUBLIC_DEMO_DEFAULT_PASSWORD ?? "Demo#123456";
  },

  async signInWithPassword(email: string, password: string): Promise<{ error: string | null }> {
    const supabase = createSupabaseBrowserClient();
    const { error } = await supabase.auth.signInWithPassword({
      email,
      password
    });
    return {
      error: error?.message ?? null
    };
  },

  async signOut(): Promise<void> {
    const supabase = createSupabaseBrowserClient();
    await supabase.auth.signOut();
  },

  async getSession(): Promise<Session | null> {
    const supabase = createSupabaseBrowserClient();
    const {
      data: { session }
    } = await supabase.auth.getSession();
    return session;
  },

  async getCurrentUser(): Promise<User | null> {
    const supabase = createSupabaseBrowserClient();
    const {
      data: { user }
    } = await supabase.auth.getUser();
    if (!user) return null;
    return fetchProfileById(user.id, user.email);
  },

  async getUserById(userId: string, fallbackEmail?: string): Promise<User | null> {
    return fetchProfileById(userId, fallbackEmail);
  },

  onAuthStateChange(callback: (event: AuthChangeEvent, session: Session | null) => void): () => void {
    const supabase = createSupabaseBrowserClient();
    const {
      data: { subscription }
    } = supabase.auth.onAuthStateChange(callback);
    return () => {
      subscription.unsubscribe();
    };
  },

  async getCurrentProfileRow(): Promise<Database["public"]["Tables"]["profiles"]["Row"] | null> {
    const supabase = createSupabaseBrowserClient();
    const {
      data: { user }
    } = await supabase.auth.getUser();
    if (!user) return null;

    const { data } = await supabase.from("profiles").select("*").eq("id", user.id).maybeSingle();
    return (data ?? null) as ProfileRow | null;
  }
};
