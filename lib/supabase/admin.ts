import { createClient } from "@supabase/supabase-js";

import type { Database } from "@/types/database";

function getAdminEnv(): { url: string; serviceRoleKey: string } {
  return {
    url: process.env.NEXT_PUBLIC_SUPABASE_URL ?? "",
    serviceRoleKey: process.env.SUPABASE_SERVICE_ROLE_KEY ?? ""
  };
}

export function hasSupabaseAdminEnv(): boolean {
  const { url, serviceRoleKey } = getAdminEnv();
  return Boolean(url && serviceRoleKey);
}

export function createSupabaseAdminClient() {
  const { url, serviceRoleKey } = getAdminEnv();
  if (!url || !serviceRoleKey) {
    throw new Error("supabase_admin_env_missing");
  }

  return createClient<Database>(url, serviceRoleKey, {
    auth: {
      autoRefreshToken: false,
      persistSession: false
    }
  });
}
