import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { createCollaborationThread } from "@/services/collaboration-thread-service";
import { getDealRoomDetail } from "@/services/deal-command-service";

interface RouteParams {
  params: { id: string };
}

const requestSchema = z.object({
  threadType: z.enum(["strategy", "blocker", "quote_review", "next_step", "risk_discussion", "manager_intervention", "playbook_application"]),
  title: z.string().min(1).max(120),
  summary: z.string().max(500).optional()
});

export async function POST(request: Request, { params }: RouteParams) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    const detail = await getDealRoomDetail({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      dealRoomId: params.id
    });
    if (!detail) return fail("Deal room not found", 404);

    if (!isManager(auth.profile)) {
      const isParticipant = detail.participants.some((item) => item.userId === auth.profile.id && item.isActive);
      if (!isParticipant && detail.room.ownerId !== auth.profile.id) {
        return fail("Sales can only operate own/joined deal rooms", 403);
      }
    }

    const thread = await createCollaborationThread({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      dealRoomId: params.id,
      threadType: parsed.data.threadType,
      title: parsed.data.title,
      summary: parsed.data.summary,
      createdBy: auth.profile.id
    });
    return ok(thread);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "create_thread_failed", 500);
  }
}

