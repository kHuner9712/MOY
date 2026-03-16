"use client";

import { createBrowserClient } from "@supabase/ssr";

import { getSupabasePublicEnv, hasSupabasePublicEnv } from "@/lib/supabase/env";
import type { Database } from "@/types/database";

type BrowserClient = ReturnType<typeof createBrowserClient<Database>>;

let browserClient: BrowserClient | null = null;

export function createSupabaseBrowserClient(): BrowserClient {
  if (browserClient) return browserClient;

  const { url, anonKey } = getSupabasePublicEnv();
  if (!hasSupabasePublicEnv()) {
    throw new Error("Missing Supabase env. Please configure NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY.");
  }

  browserClient = createBrowserClient<Database>(url, anonKey);
  return browserClient;
}
