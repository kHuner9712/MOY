"use client";

import { createBrowserClient } from "@supabase/ssr";

import { getSupabasePublicEnv, hasSupabasePublicEnv } from "@/lib/supabase/env";
import type { Database } from "@/types/database";

type BrowserClient = ReturnType<typeof createBrowserClient<Database>>;

let browserClient: BrowserClient | null = null;

export function createSupabaseBrowserClient(): BrowserClient {
  if (browserClient) return browserClient;

  if (!hasSupabasePublicEnv()) {
    throw new Error(
      "[env:supabase_public_client] Missing required environment variables: NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY."
    );
  }
  const { url, anonKey } = getSupabasePublicEnv();

  browserClient = createBrowserClient<Database>(url, anonKey);
  return browserClient;
}
