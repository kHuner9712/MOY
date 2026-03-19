import { createServerClient, type CookieOptions } from "@supabase/ssr";
import { cookies } from "next/headers";

import { getSupabasePublicEnv, hasSupabasePublicEnv } from "@/lib/supabase/env";
import type { Database } from "@/types/database";

type ServerClient = ReturnType<typeof createServerClient<Database>>;

export function createSupabaseServerClient(): ServerClient | null {
  if (!hasSupabasePublicEnv()) return null;
  const { url, anonKey } = getSupabasePublicEnv();

  const cookieStore = cookies();

  return createServerClient<Database>(url, anonKey, {
    cookies: {
      get(name: string): string | undefined {
        return cookieStore.get(name)?.value;
      },
      set(name: string, value: string, options: CookieOptions): void {
        try {
          cookieStore.set({ name, value, ...options });
        } catch {
          // `set` may throw in read-only contexts (server components without response mutation).
        }
      },
      remove(name: string, options: CookieOptions): void {
        try {
          cookieStore.set({ name, value: "", ...options, maxAge: 0 });
        } catch {
          // same as above
        }
      }
    }
  });
}
