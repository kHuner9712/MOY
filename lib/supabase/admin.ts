import { createClient } from "@supabase/supabase-js";

import { getSupabaseServiceEnvOrThrow, hasSupabaseServiceEnv } from "@/lib/env";
import type { Database } from "@/types/database";

function getAdminEnv(): { url: string; serviceRoleKey: string } {
  return getSupabaseServiceEnvOrThrow("supabase_admin_client");
}

export function hasSupabaseAdminEnv(): boolean {
  return hasSupabaseServiceEnv();
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
