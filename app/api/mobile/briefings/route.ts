import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { getMobileBriefingsView } from "@/services/mobile-brief-service";

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const url = new URL(request.url);
  const date = url.searchParams.get("date") ?? undefined;

  try {
    const result = await getMobileBriefingsView({
      supabase: auth.supabase,
      profile: auth.profile,
      date
    });
    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "mobile_briefings_failed", 500);
  }
}
