import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { addEmailMessage, createEmailThread, getEmailThreadById } from "@/services/email-thread-service";

const requestSchema = z.object({
  threadId: z.string().uuid().optional(),
  ownerId: z.string().uuid().optional(),
  customerId: z.string().uuid().optional(),
  opportunityId: z.string().uuid().optional(),
  dealRoomId: z.string().uuid().optional(),
  subject: z.string().min(1).max(300).optional(),
  participants: z.array(z.string().min(1)).optional(),
  summary: z.string().max(2000).optional(),
  direction: z.enum(["inbound", "outbound", "draft"]).optional(),
  messageSubject: z.string().max(300).optional(),
  messageBodyText: z.string().max(20000).optional(),
  messageBodyMarkdown: z.string().max(30000).optional(),
  status: z.enum(["draft", "sent", "received", "failed"]).optional()
});

function inferStatus(direction: "inbound" | "outbound" | "draft", status?: "draft" | "sent" | "received" | "failed"): "draft" | "sent" | "received" | "failed" {
  if (status) return status;
  if (direction === "inbound") return "received";
  if (direction === "draft") return "draft";
  return "sent";
}

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  const payload = parsed.data;
  const ownerId = isManager(auth.profile) ? payload.ownerId ?? auth.profile.id : auth.profile.id;

  if (!payload.threadId && !payload.subject) return fail("Subject is required when creating a new thread", 400);

  try {
    if (!isManager(auth.profile) && payload.customerId) {
      const customerRes = await auth.supabase
        .from("customers")
        .select("owner_id")
        .eq("org_id", auth.profile.org_id)
        .eq("id", payload.customerId)
        .maybeSingle();
      if (customerRes.error) throw new Error(customerRes.error.message);
      const customerOwnerId = (customerRes.data as { owner_id: string } | null)?.owner_id ?? null;
      if (!customerOwnerId || customerOwnerId !== auth.profile.id) return fail("No permission for this customer", 403);
    }

    let threadId = payload.threadId ?? null;
    if (threadId) {
      const thread = await getEmailThreadById({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        threadId
      });
      if (!thread) return fail("Email thread not found", 404);
      if (!isManager(auth.profile) && thread.ownerId !== auth.profile.id) return fail("Sales can only operate own threads", 403);
    }

    if (!threadId) {
      const thread = await createEmailThread({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        ownerId,
        customerId: payload.customerId ?? null,
        opportunityId: payload.opportunityId ?? null,
        dealRoomId: payload.dealRoomId ?? null,
        subject: payload.subject ?? "Untitled email thread",
        participants: payload.participants ?? [],
        summary: payload.summary ?? ""
      });
      threadId = thread.id;
    }

    let message = null;
    if (payload.messageBodyText && threadId) {
      const direction = payload.direction ?? "outbound";
      message = await addEmailMessage({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        threadId,
        senderUserId: auth.profile.id,
        direction,
        messageSubject: payload.messageSubject ?? payload.subject ?? "Untitled message",
        messageBodyText: payload.messageBodyText,
        messageBodyMarkdown: payload.messageBodyMarkdown ?? payload.messageBodyText,
        status: inferStatus(direction, payload.status),
        sourceType: "manual",
        triggerRuleReview: true
      });
    }

    return ok({
      threadId,
      message
    });
  } catch (error) {
    return fail(error instanceof Error ? error.message : "create_email_thread_failed", 500);
  }
}
