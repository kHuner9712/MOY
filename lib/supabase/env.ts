import { getSupabasePublicEnvOrThrow, hasSupabasePublicEnv as hasSupabasePublicEnvValidated } from "@/lib/env";

export interface SupabasePublicEnv {
  url: string;
  anonKey: string;
}

export function getSupabasePublicEnv(): SupabasePublicEnv {
  return getSupabasePublicEnvOrThrow("supabase_public_client");
}

export function hasSupabasePublicEnv(): boolean {
  return hasSupabasePublicEnvValidated();
}
