import { buildFallbackEmailDraft } from "@/lib/external-touchpoint-fallback";
import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { addEmailMessage, createEmailThread, getEmailThreadById } from "@/services/email-thread-service";
import { mapContentDraftRow } from "@/services/mappers";
import type { EmailDraftGenerationResult } from "@/types/ai";
import { emailDraftGenerationResultSchema } from "@/types/ai";
import type { Database } from "@/types/database";
import type { ContentDraft } from "@/types/preparation";

type DbClient = ServerSupabaseClient;
type ProfileRow = Database["public"]["Tables"]["profiles"]["Row"];

function nowIso(): string {
  return new Date().toISOString();
}

function mapContextToDraftType(
  contextType: "followup" | "quote" | "meeting_confirm" | "meeting_followup" | "manager_support"
): Database["public"]["Enums"]["content_draft_type"] {
  if (contextType === "quote") return "quote_explanation";
  if (contextType === "meeting_confirm") return "meeting_opening";
  if (contextType === "meeting_followup") return "meeting_summary";
  if (contextType === "manager_support") return "manager_checkin_note";
  return "followup_message";
}

function buildMarkdown(result: EmailDraftGenerationResult): string {
  return [`## ${result.subject}`, "", result.opening, "", result.body, "", `**Next Step**: ${result.cta}`].join("\n");
}

export async function generateEmailDraft(params: {
  supabase: DbClient;
  profile: ProfileRow;
  contextType: "followup" | "quote" | "meeting_confirm" | "meeting_followup" | "manager_support";
  customerId?: string | null;
  opportunityId?: string | null;
  dealRoomId?: string | null;
  threadId?: string | null;
  extraInstruction?: string | null;
}): Promise<{
  threadId: string;
  messageId: string;
  contentDraft: ContentDraft;
  result: EmailDraftGenerationResult;
  runId: string;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: false });

  const customerRes = params.customerId
    ? await params.supabase
        .from("customers")
        .select("id, company_name, current_stage, win_probability, risk_level, owner_id, ai_summary, ai_suggestion")
        .eq("org_id", params.profile.org_id)
        .eq("id", params.customerId)
        .maybeSingle()
    : { data: null, error: null };
  if (customerRes.error) throw new Error(customerRes.error.message);

  const dealRes = params.dealRoomId
    ? await params.supabase
        .from("deal_rooms")
        .select("id, title, room_status, current_goal, current_blockers, next_milestone, manager_attention_needed")
        .eq("org_id", params.profile.org_id)
        .eq("id", params.dealRoomId)
        .maybeSingle()
    : { data: null, error: null };
  if (dealRes.error) throw new Error(dealRes.error.message);

  const decisionRes = params.dealRoomId
    ? await params.supabase
        .from("decision_records")
        .select("id, decision_type, status, title, decision_reason")
        .eq("org_id", params.profile.org_id)
        .eq("deal_room_id", params.dealRoomId)
        .order("updated_at", { ascending: false })
        .limit(5)
    : { data: [], error: null };
  if (decisionRes.error) throw new Error(decisionRes.error.message);

  const checkpointRes = params.dealRoomId
    ? await params.supabase
        .from("deal_checkpoints")
        .select("id, checkpoint_type, status, title, due_at")
        .eq("org_id", params.profile.org_id)
        .eq("deal_room_id", params.dealRoomId)
        .order("updated_at", { ascending: false })
        .limit(8)
    : { data: [], error: null };
  if (checkpointRes.error) throw new Error(checkpointRes.error.message);

  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    scenario: "email_draft_generation",
    providerId: provider.id
  });

  const inputSnapshot: Record<string, unknown> = {
    context_type: params.contextType,
    customer: customerRes.data ?? null,
    deal_room: dealRes.data ?? null,
    recent_decisions: decisionRes.data ?? [],
    recent_checkpoints: checkpointRes.data ?? [],
    extra_instruction: params.extraInstruction ?? ""
  };

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    customerId: params.customerId ?? null,
    followupId: null,
    triggeredByUserId: params.profile.id,
    triggerSource: "manual",
    scenario: "email_draft_generation",
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    inputSnapshot
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: nowIso()
  });

  let usedFallback = false;
  let fallbackReason: string | null = null;
  let outputSnapshot: Record<string, unknown> = {};
  let parsedResult: EmailDraftGenerationResult;
  let responseModel = model;
  const startedAt = Date.now();

  try {
    if (!provider.isConfigured()) throw new Error("provider_not_configured");

    const response = await provider.chatCompletion({
      scenario: "email_draft_generation",
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        scenario: "email_draft_generation",
        payload: inputSnapshot
      }),
      jsonMode: true,
      strictMode: true,
      useReasonerModel: false
    });

    outputSnapshot = response.rawResponse;
    responseModel = response.model;
    if (response.error) throw new Error(response.error);
    const candidate = response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null);
    const parsed = emailDraftGenerationResultSchema.safeParse(candidate);
    if (!parsed.success) throw new Error("email_draft_generation_schema_invalid");
    parsedResult = parsed.data;
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "email_draft_generation_failed";
      await updateAiRunStatus({
        supabase: params.supabase,
        runId: run.id,
        status: "failed",
        provider: provider.id,
        model,
        errorMessage: message,
        completedAt: nowIso()
      });
      throw error;
    }
    usedFallback = true;
    fallbackReason = error instanceof Error ? error.message : "email_draft_generation_fallback";
    parsedResult = buildFallbackEmailDraft({
      customerName: customerRes.data?.company_name ?? "客户",
      context: params.contextType
    });
    outputSnapshot = { fallback: true, reason: fallbackReason };
    responseModel = "rule-fallback";
  }

  let threadId = params.threadId ?? null;
  if (threadId) {
    const exists = await getEmailThreadById({
      supabase: params.supabase,
      orgId: params.profile.org_id,
      threadId
    });
    if (!exists) threadId = null;
  }

  if (!threadId) {
    const createdThread = await createEmailThread({
      supabase: params.supabase,
      orgId: params.profile.org_id,
      ownerId: params.profile.id,
      customerId: params.customerId ?? null,
      opportunityId: params.opportunityId ?? null,
      dealRoomId: params.dealRoomId ?? null,
      subject: parsedResult.subject,
      participants: [],
      summary: parsedResult.body,
      sourceSnapshot: inputSnapshot
    });
    threadId = createdThread.id;
  }

  const message = await addEmailMessage({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    threadId,
    senderUserId: params.profile.id,
    direction: "draft",
    messageSubject: parsedResult.subject,
    messageBodyText: `${parsedResult.opening}\n\n${parsedResult.body}\n\n${parsedResult.cta}`,
    messageBodyMarkdown: buildMarkdown(parsedResult),
    status: "draft",
    sourceType: "ai_generated",
    aiRunId: run.id
  });

  const draftPayload: Database["public"]["Tables"]["content_drafts"]["Insert"] = {
    org_id: params.profile.org_id,
    owner_id: params.profile.id,
    customer_id: params.customerId ?? null,
    opportunity_id: params.opportunityId ?? null,
    prep_card_id: null,
    work_item_id: null,
    draft_type: mapContextToDraftType(params.contextType),
    status: "draft",
    title: parsedResult.subject,
    content_markdown: buildMarkdown(parsedResult),
    content_text: `${parsedResult.opening}\n${parsedResult.body}\n${parsedResult.cta}`,
    rationale: `Generated from email_draft_generation (${params.contextType}).`,
    source_snapshot: {
      ...inputSnapshot,
      email_thread_id: threadId,
      email_message_id: message.id,
      caution_notes: parsedResult.caution_notes
    } as Database["public"]["Tables"]["content_drafts"]["Insert"]["source_snapshot"],
    generated_by: params.profile.id,
    ai_run_id: run.id
  };

  const draftRes = await params.supabase.from("content_drafts").insert(draftPayload).select("*").single();
  if (draftRes.error || !draftRes.data) throw new Error(draftRes.error?.message ?? "create_email_content_draft_failed");
  const contentDraft = mapContentDraftRow(draftRes.data as Database["public"]["Tables"]["content_drafts"]["Row"]);

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "completed",
    provider: provider.id,
    model: responseModel,
    outputSnapshot,
    parsedResult: parsedResult as Record<string, unknown>,
    resultSource: usedFallback ? "fallback" : "provider",
    fallbackReason,
    errorMessage: usedFallback ? fallbackReason : null,
    latencyMs: Date.now() - startedAt,
    completedAt: nowIso()
  });

  return {
    threadId,
    messageId: message.id,
    contentDraft,
    result: parsedResult,
    runId: run.id,
    usedFallback,
    fallbackReason
  };
}

