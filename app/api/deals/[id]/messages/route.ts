import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { addThreadMessage } from "@/services/collaboration-thread-service";
import { getDealRoomDetail } from "@/services/deal-command-service";

interface RouteParams {
  params: { id: string };
}

const requestSchema = z.object({
  threadId: z.string().uuid(),
  bodyMarkdown: z.string().min(1),
  messageType: z.enum(["comment", "decision_note", "ai_summary", "system_event"]).optional(),
  mentions: z.array(z.string().uuid()).optional(),
  sourceRefType: z.string().max(80).optional(),
  sourceRefId: z.string().uuid().optional()
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

    if (!detail.threads.some((item) => item.id === parsed.data.threadId)) {
      return fail("Thread does not belong to this deal room", 400);
    }

    if (!isManager(auth.profile)) {
      const isParticipant = detail.participants.some((item) => item.userId === auth.profile.id && item.isActive);
      if (!isParticipant && detail.room.ownerId !== auth.profile.id) {
        return fail("Sales can only operate own/joined deal rooms", 403);
      }
    }

    const message = await addThreadMessage({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      threadId: parsed.data.threadId,
      authorUserId: auth.profile.id,
      messageType: parsed.data.messageType ?? "comment",
      bodyMarkdown: parsed.data.bodyMarkdown,
      mentions: parsed.data.mentions ?? [],
      sourceRefType: parsed.data.sourceRefType ?? null,
      sourceRefId: parsed.data.sourceRefId ?? null
    });
    return ok(message);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "add_message_failed", 500);
  }
}

