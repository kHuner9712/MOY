import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { getMobileBootstrap } from "@/services/mobile-shell-service";

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const url = new URL(request.url);
  const date = url.searchParams.get("date") ?? undefined;

  try {
    const data = await getMobileBootstrap({
      supabase: auth.supabase,
      profile: auth.profile,
      date
    });
    return ok(data);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "mobile_bootstrap_failed", 500);
  }
}
