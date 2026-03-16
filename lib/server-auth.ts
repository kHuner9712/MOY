import { createSupabaseServerClient } from "@/lib/supabase/server";
import type { Database } from "@/types/database";

export interface ServerAuthContext {
  supabase: NonNullable<ReturnType<typeof createSupabaseServerClient>>;
  user: { id: string; email?: string };
  profile: Database["public"]["Tables"]["profiles"]["Row"];
}

export async function getServerAuthContext(): Promise<ServerAuthContext | null> {
  const supabase = createSupabaseServerClient();
  if (!supabase) return null;

  const {
    data: { user }
  } = await supabase.auth.getUser();

  if (!user) return null;

  const { data: profileRaw } = await supabase.from("profiles").select("*").eq("id", user.id).maybeSingle();
  const profile = profileRaw as Database["public"]["Tables"]["profiles"]["Row"] | null;
  if (!profile || !profile.is_active) return null;

  const membershipRes = await supabase
    .from("org_memberships")
    .select("role, seat_status")
    .eq("org_id", profile.org_id)
    .eq("user_id", profile.id)
    .maybeSingle();
  if (membershipRes.error) return null;

  const membership = (membershipRes.data ?? null) as
    | Pick<Database["public"]["Tables"]["org_memberships"]["Row"], "role" | "seat_status">
    | null;

  if (membership && membership.seat_status !== "active") return null;

  const effectiveRole = membership
    ? membership.role === "owner" || membership.role === "admin" || membership.role === "manager"
      ? "manager"
      : "sales"
    : profile.role;

  return {
    supabase,
    user: {
      id: user.id,
      email: user.email ?? undefined
    },
    profile: {
      ...profile,
      role: effectiveRole
    }
  };
}
