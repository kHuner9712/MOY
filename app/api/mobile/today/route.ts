import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { getMobileTodayLite } from "@/services/mobile-shell-service";

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const url = new URL(request.url);
  const date = url.searchParams.get("date") ?? undefined;

  try {
    const result = await getMobileTodayLite({
      supabase: auth.supabase,
      profile: auth.profile,
      date
    });
    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "mobile_today_failed", 500);
  }
}
