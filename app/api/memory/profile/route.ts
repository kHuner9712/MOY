import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { getUserMemoryProfile, listUserMemoryItems } from "@/services/user-memory-service";

function sanitizeItemsForManager(items: Awaited<ReturnType<typeof listUserMemoryItems>>) {
  return items.map((item) => ({
    id: item.id,
    memoryType: item.memoryType,
    title: item.title,
    description: item.description,
    confidenceScore: item.confidenceScore,
    sourceCount: item.sourceCount,
    status: item.status,
    createdAt: item.createdAt,
    updatedAt: item.updatedAt
  }));
}

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const url = new URL(request.url);
  const targetUserId = url.searchParams.get("userId") ?? auth.profile.id;

  if (!isManager(auth.profile) && targetUserId !== auth.profile.id) {
    return fail("Sales can only view personal memory", 403);
  }

  try {
    const [profile, items] = await Promise.all([
      getUserMemoryProfile({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        userId: targetUserId
      }),
      listUserMemoryItems({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        userId: targetUserId,
        includeHidden: targetUserId === auth.profile.id,
        limit: 80
      })
    ]);

    return ok({
      profile,
      items: isManager(auth.profile) && targetUserId !== auth.profile.id ? sanitizeItemsForManager(items) : items
    });
  } catch (error) {
    return fail(error instanceof Error ? error.message : "memory_profile_failed", 500);
  }
}
