import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { buildFallbackMobileQuickCaptureRefine } from "@/lib/mobile-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { captureActionOutcome } from "@/services/action-outcome-service";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { extractCommunicationInput } from "@/services/communication-extraction-service";
import { createEmailThread, addEmailMessage } from "@/services/email-thread-service";
import {
  createMobileDraftSyncJob,
  enqueueOfflineAction,
  updateMobileDraftSyncJob
} from "@/services/mobile-draft-service";
import { mobileQuickCaptureRefineResultSchema, type MobileQuickCaptureRefineResult } from "@/types/ai";
import type { Database } from "@/types/database";

type DbClient = ServerSupabaseClient;
type ProfileRow = Database["public"]["Tables"]["profiles"]["Row"];

function nowIso(): string {
  return new Date().toISOString();
}

export async function runMobileQuickCaptureRefine(params: {
  supabase: DbClient;
  profile: ProfileRow;
  rawInput: string;
  customerId?: string | null;
  dealRoomId?: string | null;
}): Promise<{
  result: MobileQuickCaptureRefineResult;
  runId: string;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: false });
  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    scenario: "mobile_quick_capture_refine",
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    customerId: params.customerId ?? null,
    followupId: null,
    triggeredByUserId: params.profile.id,
    triggerSource: "manual",
    scenario: "mobile_quick_capture_refine",
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    inputSnapshot: {
      raw_input: params.rawInput,
      customer_id: params.customerId ?? null,
      deal_room_id: params.dealRoomId ?? null
    }
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: nowIso()
  });

  let usedFallback = false;
  let fallbackReason: string | null = null;
  let modelUsed = model;
  let result = buildFallbackMobileQuickCaptureRefine({
    rawInput: params.rawInput,
    hasCustomerContext: Boolean(params.customerId)
  });
  let outputSnapshot: Record<string, unknown> = {};
  const started = Date.now();

  try {
    if (!provider.isConfigured()) throw new Error("provider_not_configured");
    const response = await provider.chatCompletion({
      scenario: "mobile_quick_capture_refine",
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        scenario: "mobile_quick_capture_refine",
        payload: {
          raw_input: params.rawInput,
          customer_id: params.customerId ?? null,
          deal_room_id: params.dealRoomId ?? null
        }
      }),
      jsonMode: true,
      strictMode: true
    });
    if (response.error) throw new Error(response.error);
    const parsed = mobileQuickCaptureRefineResultSchema.safeParse(
      response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null)
    );
    if (!parsed.success) throw new Error("mobile_quick_capture_refine_schema_invalid");
    result = parsed.data;
    modelUsed = response.model;
    outputSnapshot = response.rawResponse;
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "mobile_quick_capture_refine_failed";
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
    fallbackReason = error instanceof Error ? error.message : "mobile_quick_capture_refine_fallback";
    modelUsed = "rule-fallback";
    outputSnapshot = {
      fallback: true,
      reason: fallbackReason
    };
  }

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "completed",
    provider: provider.id,
    model: modelUsed,
    outputSnapshot,
    parsedResult: result,
    resultSource: usedFallback ? "fallback" : "provider",
    fallbackReason,
    latencyMs: Date.now() - started,
    completedAt: nowIso(),
    errorMessage: usedFallback ? fallbackReason : null
  });

  return {
    result,
    runId: run.id,
    usedFallback,
    fallbackReason
  };
}

export async function syncMobileDraft(params: {
  supabase: DbClient;
  profile: ProfileRow;
  localDraftId: string;
  draftType: Database["public"]["Enums"]["mobile_draft_type"];
  summary?: string | null;
  payload: Record<string, unknown>;
}): Promise<{
  syncJobId: string;
  syncStatus: "synced" | "failed";
  targetEntityType: string | null;
  targetEntityId: string | null;
  message: string;
}> {
  const syncJob = await createMobileDraftSyncJob({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    userId: params.profile.id,
    localDraftId: params.localDraftId,
    draftType: params.draftType,
    summary: params.summary ?? null,
    payloadSnapshot: params.payload,
    syncStatus: "pending"
  });

  const queueActionType: Database["public"]["Enums"]["offline_action_type"] =
    params.draftType === "capture"
      ? "create_capture_draft"
      : params.draftType === "outcome"
        ? "create_outcome_draft"
        : params.draftType === "email_draft"
          ? "save_email_draft"
          : "create_capture_draft";
  await enqueueOfflineAction({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    userId: params.profile.id,
    actionType: queueActionType,
    actionPayload: params.payload,
    queueStatus: "processing",
    targetEntityType: null,
    targetEntityId: null
  }).catch(() => null);

  try {
    let targetEntityType: string | null = null;
    let targetEntityId: string | null = null;
    let message = "Draft synced.";

    if (params.draftType === "capture") {
      const rawContent = typeof params.payload.rawContent === "string" ? params.payload.rawContent : "";
      if (!rawContent.trim()) throw new Error("capture_raw_content_required");
      const sourceType = (params.payload.sourceType as Database["public"]["Enums"]["communication_source_type"] | undefined) ?? "manual_note";
      const result = await extractCommunicationInput({
        supabase: params.supabase,
        profile: {
          id: params.profile.id,
          org_id: params.profile.org_id,
          role: params.profile.role
        },
        sourceType,
        title: typeof params.payload.title === "string" ? params.payload.title : "Mobile capture",
        rawContent,
        customerId: typeof params.payload.customerId === "string" ? params.payload.customerId : null
      });
      targetEntityType = "communication_input";
      targetEntityId = result.inputId;
      message =
        result.extractionStatus === "completed"
          ? result.autoApplied
            ? "Capture synced and auto-applied."
            : "Capture synced."
          : result.extractionError ?? "Capture synced with extraction issues.";
    } else if (params.draftType === "outcome") {
      const result = await captureActionOutcome({
        supabase: params.supabase,
        profile: params.profile,
        workItemId: typeof params.payload.workItemId === "string" ? params.payload.workItemId : null,
        customerId: typeof params.payload.customerId === "string" ? params.payload.customerId : null,
        outcomeType: "task_result",
        keyOutcomeSummary: typeof params.payload.keyOutcomeSummary === "string" ? params.payload.keyOutcomeSummary : "Mobile quick outcome",
        nextStepText: typeof params.payload.nextStepText === "string" ? params.payload.nextStepText : null,
        autoInfer: true
      });
      targetEntityType = "action_outcome";
      targetEntityId = result.outcome.id;
      message = result.usedFallback
        ? "Outcome synced with fallback inference."
        : "Outcome synced.";
    } else if (params.draftType === "email_draft") {
      const customerId = typeof params.payload.customerId === "string" ? params.payload.customerId : null;
      const subject = typeof params.payload.subject === "string" ? params.payload.subject : "Mobile draft email";
      const body = typeof params.payload.body === "string" ? params.payload.body : "";
      if (!customerId) throw new Error("email_draft_customer_required");
      const thread = await createEmailThread({
        supabase: params.supabase,
        orgId: params.profile.org_id,
        ownerId: params.profile.id,
        customerId,
        opportunityId: typeof params.payload.opportunityId === "string" ? params.payload.opportunityId : null,
        dealRoomId: typeof params.payload.dealRoomId === "string" ? params.payload.dealRoomId : null,
        subject,
        summary: body.slice(0, 180)
      });
      const messageRow = await addEmailMessage({
        supabase: params.supabase,
        orgId: params.profile.org_id,
        threadId: thread.id,
        senderUserId: params.profile.id,
        direction: "draft",
        messageSubject: subject,
        messageBodyText: body,
        messageBodyMarkdown: body,
        status: "draft",
        sourceType: "manual"
      });
      targetEntityType = "email_message";
      targetEntityId = messageRow.id;
      message = "Email draft synced to touchpoints.";
    } else {
      targetEntityType = "touchpoint_note";
      targetEntityId = null;
      message = "Touchpoint note stored for manual follow-up.";
    }

    await updateMobileDraftSyncJob({
      supabase: params.supabase,
      orgId: params.profile.org_id,
      userId: params.profile.id,
      jobId: syncJob.id,
      patch: {
        sync_status: "synced",
        target_entity_type: targetEntityType,
        target_entity_id: targetEntityId,
        error_message: null
      }
    });

    return {
      syncJobId: syncJob.id,
      syncStatus: "synced",
      targetEntityType,
      targetEntityId,
      message
    };
  } catch (error) {
    const reason = error instanceof Error ? error.message : "mobile_draft_sync_failed";
    await updateMobileDraftSyncJob({
      supabase: params.supabase,
      orgId: params.profile.org_id,
      userId: params.profile.id,
      jobId: syncJob.id,
      patch: {
        sync_status: "failed",
        error_message: reason
      }
    });
    return {
      syncJobId: syncJob.id,
      syncStatus: "failed",
      targetEntityType: null,
      targetEntityId: null,
      message: reason
    };
  }
}
