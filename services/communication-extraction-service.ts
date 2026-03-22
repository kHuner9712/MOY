import { getAiProvider } from "@/lib/ai/provider";
import { decideCaptureApplyMode } from "@/lib/capture-flow";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { runFollowupAnalysis } from "@/services/ai-analysis-service";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { upsertCaptureBusinessEvents } from "@/services/business-event-service";
import { matchCustomer } from "@/services/customer-match-service";
import {
  communicationExtractionResultSchema,
  type AiProviderId,
  type AiScenario,
  type CommunicationExtractionResult,
  type CommunicationType
} from "@/types/ai";
import type {
  CaptureConfirmResult,
  CaptureDownstreamTrace,
  CaptureExtractResult,
  CommunicationSourceType
} from "@/types/communication";
import type { Database } from "@/types/database";

type DbClient = ServerSupabaseClient;
type CommunicationInputRow = Database["public"]["Tables"]["communication_inputs"]["Row"];
type CustomerRow = Database["public"]["Tables"]["customers"]["Row"];
type FollowupDraftStatus = Database["public"]["Enums"]["followup_draft_status"];

interface AuthLikeProfile {
  id: string;
  org_id: string;
  role: "sales" | "manager";
}

function nowIso(): string {
  return new Date().toISOString();
}

function toIsoOrNull(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toISOString();
}

function normalizeText(value: string | null | undefined): string {
  return (value ?? "").trim();
}

function toCommunicationType(value: CommunicationType): Database["public"]["Enums"]["communication_type"] {
  return value;
}

export function decideExtractionApplyMode(params: {
  shouldCreateFollowup: boolean;
  hasMatchedCustomer: boolean;
  extractionConfidence: number;
  matchConfidence: number;
  hasSummary: boolean;
  hasNextStep: boolean;
}): "auto" | "manual" | "none" {
  return decideCaptureApplyMode(params);
}

function toFollowupPayload(params: {
  orgId: string;
  customerId: string;
  ownerId: string;
  actorId: string;
  extraction: CommunicationExtractionResult;
  occurredAt: string;
  sourceInputId: string;
  draftStatus: FollowupDraftStatus;
}): Database["public"]["Tables"]["followups"]["Insert"] {
  const needs = params.extraction.key_needs.length > 0 ? params.extraction.key_needs.join("；") : params.extraction.summary;
  const objections = params.extraction.key_objections.length > 0 ? params.extraction.key_objections.join("；") : null;

  return {
    org_id: params.orgId,
    customer_id: params.customerId,
    owner_id: params.ownerId,
    communication_type: toCommunicationType(params.extraction.communication_type),
    summary: params.extraction.summary,
    customer_needs: needs,
    objections,
    next_step: normalizeText(params.extraction.next_step) || "继续确认需求并推动下一次沟通",
    next_followup_at: toIsoOrNull(params.extraction.recommended_next_followup_at),
    needs_ai_analysis: params.draftStatus === "confirmed",
    source_input_id: params.sourceInputId,
    draft_status: params.draftStatus,
    created_by: params.actorId,
    created_at: params.occurredAt
  };
}

async function createFollowupFromExtraction(params: {
  supabase: DbClient;
  orgId: string;
  actorId: string;
  customer: CustomerRow;
  extraction: CommunicationExtractionResult;
  occurredAt: string;
  sourceInputId: string;
  draftStatus: FollowupDraftStatus;
}): Promise<string> {
  const payload = toFollowupPayload({
    orgId: params.orgId,
    customerId: params.customer.id,
    ownerId: params.customer.owner_id,
    actorId: params.actorId,
    extraction: params.extraction,
    occurredAt: params.occurredAt,
    sourceInputId: params.sourceInputId,
    draftStatus: params.draftStatus
  });

  const { data, error } = await params.supabase.from("followups").insert(payload).select("id").single();
  if (error || !data) throw new Error(error?.message ?? "Failed to create followup from extraction");
  return data.id;
}

async function patchOpportunityFromExtraction(params: {
  supabase: DbClient;
  customerId: string;
  extraction: CommunicationExtractionResult;
}): Promise<void> {
  if (!params.extraction.should_update_opportunity) return;

  const { data: latest, error: latestError } = await params.supabase
    .from("opportunities")
    .select("id, risk_level")
    .eq("customer_id", params.customerId)
    .order("updated_at", { ascending: false })
    .limit(1)
    .maybeSingle();

  if (latestError || !latest) return;

  const nextRisk: Database["public"]["Enums"]["risk_level"] =
    params.extraction.key_objections.length >= 2 ? "high" : params.extraction.key_objections.length === 1 ? "medium" : latest.risk_level;

  await params.supabase
    .from("opportunities")
    .update({
      last_activity_at: nowIso(),
      risk_level: nextRisk
    })
    .eq("id", latest.id);
}

async function createAiRunForExtraction(params: {
  supabase: DbClient;
  orgId: string;
  actorId: string;
  customerId?: string | null;
  promptVersion: string;
  providerId: AiProviderId;
  model: string;
  snapshot: Record<string, unknown>;
}) {
  return createAiRun({
    supabase: params.supabase,
    orgId: params.orgId,
    customerId: params.customerId ?? null,
    followupId: null,
    triggeredByUserId: params.actorId,
    triggerSource: "manual",
    scenario: "communication_extraction",
    provider: params.providerId,
    model: params.model,
    promptVersion: params.promptVersion,
    inputSnapshot: params.snapshot
  });
}

function readExtractionFromStoredData(stored: Record<string, unknown>): CommunicationExtractionResult | null {
  const raw = stored.extraction;
  const parsed = communicationExtractionResultSchema.safeParse(raw);
  if (!parsed.success) return null;
  return parsed.data;
}

function createEmptyCaptureTrace(): CaptureDownstreamTrace {
  return {
    followupAnalysisRunId: null,
    leakAlertAction: null,
    linkedWorkItemId: null,
    linkedWorkItemCreated: null,
    businessEventIds: [],
    businessEventCreatedCount: 0,
    businessEventUpdatedCount: 0,
    downstreamErrors: []
  };
}

function toCaptureTracePayload(trace: CaptureDownstreamTrace): Record<string, unknown> {
  return {
    followup_analysis_run_id: trace.followupAnalysisRunId,
    leak_alert_action: trace.leakAlertAction,
    linked_work_item_id: trace.linkedWorkItemId,
    linked_work_item_created: trace.linkedWorkItemCreated,
    business_event_ids: trace.businessEventIds,
    business_event_created_count: trace.businessEventCreatedCount,
    business_event_updated_count: trace.businessEventUpdatedCount,
    downstream_errors: trace.downstreamErrors,
    updated_at: nowIso()
  };
}

export async function extractCommunicationInput(params: {
  supabase: DbClient;
  profile: AuthLikeProfile;
  sourceType: CommunicationSourceType;
  title?: string;
  rawContent: string;
  customerId?: string | null;
  inputLanguage?: string;
  occurredAt?: string;
}): Promise<CaptureExtractResult> {
  const occurredAt = toIsoOrNull(params.occurredAt) ?? nowIso();

  const { data: created, error: createError } = await params.supabase
    .from("communication_inputs")
    .insert({
      org_id: params.profile.org_id,
      customer_id: params.customerId ?? null,
      owner_id: params.profile.id,
      source_type: params.sourceType,
      title: normalizeText(params.title) || `Capture - ${params.sourceType}`,
      raw_content: params.rawContent.trim(),
      input_language: normalizeText(params.inputLanguage) || "zh-CN",
      occurred_at: occurredAt,
      extraction_status: "processing",
      created_by: params.profile.id
    })
    .select("*")
    .single();

  if (createError || !created) {
    throw new Error(createError?.message ?? "Failed to save communication input");
  }

  const inputRow = created as CommunicationInputRow;
  const provider = getAiProvider();

  const scenario: AiScenario = "communication_extraction";
  const model = provider.getDefaultModel({ reasoning: true });
  let aiRunId: string | null = null;

  try {
    const prompt = await getActivePromptVersion({
      supabase: params.supabase,
      orgId: params.profile.org_id,
      scenario,
      providerId: provider.id
    });

    const hintCustomersQuery = params.supabase
      .from("customers")
      .select("id, name, company_name, contact_name, owner_id, current_stage, updated_at")
      .eq("org_id", params.profile.org_id)
      .order("updated_at", { ascending: false })
      .limit(12);

    const { data: hintCustomers } = await hintCustomersQuery;

    const snapshot = {
      source_type: params.sourceType,
      raw_content: params.rawContent,
      occurred_at: occurredAt,
      selected_customer_id: params.customerId ?? null,
      hint_customers: (hintCustomers ?? []).map((item: any) => ({
        id: item.id,
        name: item.name,
        company_name: item.company_name,
        contact_name: item.contact_name,
        stage: item.current_stage
      }))
    };

    const aiRun = await createAiRunForExtraction({
      supabase: params.supabase,
      orgId: params.profile.org_id,
      actorId: params.profile.id,
      customerId: params.customerId ?? null,
      promptVersion: prompt.version,
      providerId: provider.id,
      model,
      snapshot
    });
    aiRunId = aiRun.id;

    await updateAiRunStatus({
      supabase: params.supabase,
      runId: aiRun.id,
      status: "running",
      startedAt: nowIso()
    });

    const startedAt = Date.now();
    const providerResponse = await provider.chatCompletion({
      scenario,
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({ scenario, payload: snapshot }),
      jsonMode: true,
      strictMode: true,
      useReasonerModel: true
    });

    if (providerResponse.error) {
      throw new Error(providerResponse.error);
    }

    const candidate = providerResponse.parsedJson ?? (providerResponse.rawText ? (JSON.parse(providerResponse.rawText) as Record<string, unknown>) : null);
    const extractionParsed = communicationExtractionResultSchema.safeParse(candidate);

    if (!extractionParsed.success) {
      throw new Error("communication_extraction_schema_invalid");
    }

    const extraction = extractionParsed.data;

    const match = await matchCustomer({
      supabase: params.supabase,
      orgId: params.profile.org_id,
      actorId: params.profile.id,
      actorRole: params.profile.role,
      explicitCustomerId: params.customerId ?? null,
      matchedCustomerName: extraction.matched_customer_name,
      limit: 50
    });

    const mode = decideExtractionApplyMode({
      shouldCreateFollowup: extraction.should_create_followup,
      hasMatchedCustomer: Boolean(match.matchedCustomer),
      extractionConfidence: extraction.confidence_of_match,
      matchConfidence: match.confidence,
      hasSummary: normalizeText(extraction.summary).length > 0,
      hasNextStep: normalizeText(extraction.next_step).length > 0
    });

    let followupId: string | null = null;
    let draftStatus: FollowupDraftStatus | null = null;
    const downstreamTrace = createEmptyCaptureTrace();

    if (match.matchedCustomer && (mode === "auto" || mode === "manual")) {
      draftStatus = mode === "auto" ? "confirmed" : "draft";
      followupId = await createFollowupFromExtraction({
        supabase: params.supabase,
        orgId: params.profile.org_id,
        actorId: params.profile.id,
        customer: match.matchedCustomer,
        extraction,
        occurredAt,
        sourceInputId: inputRow.id,
        draftStatus
      });

      await patchOpportunityFromExtraction({
        supabase: params.supabase,
        customerId: match.matchedCustomer.id,
        extraction
      });
    }

    if (mode === "auto" && followupId && match.matchedCustomer) {
      try {
        const analysis = await runFollowupAnalysis({
          supabase: params.supabase,
          orgId: params.profile.org_id,
          customerId: match.matchedCustomer.id,
          followupId,
          triggeredByUserId: params.profile.id,
          triggerSource: "followup_submit"
        });
        downstreamTrace.followupAnalysisRunId = analysis.run.id;
        downstreamTrace.leakAlertAction = analysis.leakAlertAction;
        downstreamTrace.linkedWorkItemId = analysis.alertWorkItem?.workItemId ?? null;
        downstreamTrace.linkedWorkItemCreated = analysis.alertWorkItem?.created ?? null;
      } catch (cause) {
        downstreamTrace.downstreamErrors.push(cause instanceof Error ? cause.message : "followup_analysis_failed");
      }

      try {
        const eventResult = await upsertCaptureBusinessEvents({
          supabase: params.supabase,
          orgId: params.profile.org_id,
          input: {
            customerId: match.matchedCustomer.id,
            ownerId: match.matchedCustomer.owner_id,
            communicationInputId: inputRow.id,
            followupId,
            extraction,
            lifecycle: "capture_auto_confirmed"
          }
        });
        downstreamTrace.businessEventIds = eventResult.events.map((item) => item.id);
        downstreamTrace.businessEventCreatedCount = eventResult.createdCount;
        downstreamTrace.businessEventUpdatedCount = eventResult.updatedCount;
      } catch (cause) {
        downstreamTrace.downstreamErrors.push(cause instanceof Error ? cause.message : "capture_event_upsert_failed");
      }
    }

    const extractedData = {
      extraction,
      match: {
        customer_id: match.matchedCustomer?.id ?? null,
        customer_name: match.matchedCustomer?.company_name ?? null,
        confidence: match.confidence,
        candidates: match.candidates
      },
      apply_mode: mode,
      ai_run_id: aiRun.id,
      trace: {
        source_input_id: inputRow.id,
        followup_id: followupId,
        customer_id: match.matchedCustomer?.id ?? params.customerId ?? null,
        lifecycle: mode === "auto" ? "capture_auto_confirmed" : "capture_extract_only",
        ...toCaptureTracePayload(downstreamTrace)
      }
    };

    await params.supabase
      .from("communication_inputs")
      .update({
        customer_id: match.matchedCustomer?.id ?? params.customerId ?? null,
        extracted_followup_id: followupId,
        extraction_status: "completed",
        extraction_error: null,
        extracted_data: extractedData
      })
      .eq("id", inputRow.id);

    await updateAiRunStatus({
      supabase: params.supabase,
      runId: aiRun.id,
      status: "completed",
      provider: providerResponse.provider,
      model: providerResponse.model,
      outputSnapshot: providerResponse.rawResponse,
      parsedResult: extractedData,
      latencyMs: providerResponse.latencyMs,
      resultSource: "provider",
      completedAt: nowIso()
    });

    console.info("[capture.extract]", {
      org_id: params.profile.org_id,
      user_id: params.profile.id,
      customer_id: match.matchedCustomer?.id ?? null,
      scenario,
      provider: providerResponse.provider,
      model: providerResponse.model,
      status: "completed",
      duration_ms: Date.now() - startedAt
    });

    return {
      inputId: inputRow.id,
      extractionStatus: "completed",
      extractionError: null,
      matchedCustomerId: match.matchedCustomer?.id ?? null,
      matchedCustomerName: match.matchedCustomer?.company_name ?? null,
      confidenceOfMatch: Math.min(1, Math.max(0, Math.max(extraction.confidence_of_match, match.confidence))),
      autoApplied: mode === "auto",
      requiresConfirmation: mode === "manual" || (extraction.should_create_followup && !match.matchedCustomer),
      followupId,
      draftStatus,
      extracted: extraction,
      trace: downstreamTrace
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "communication_extraction_failed";

    if (aiRunId) {
      await updateAiRunStatus({
        supabase: params.supabase,
        runId: aiRunId,
        status: "failed",
        provider: provider.id,
        model,
        errorMessage: message,
        completedAt: nowIso()
      });
    }

    await params.supabase
      .from("communication_inputs")
      .update({
        extraction_status: "failed",
        extraction_error: message
      })
      .eq("id", inputRow.id);

    console.error("[capture.extract]", {
      org_id: params.profile.org_id,
      user_id: params.profile.id,
      customer_id: params.customerId ?? null,
      scenario,
      provider: provider.id,
      model,
      status: "failed",
      error: message
    });

    return {
      inputId: inputRow.id,
      extractionStatus: "failed",
      extractionError: message,
      matchedCustomerId: null,
      matchedCustomerName: null,
      confidenceOfMatch: null,
      autoApplied: false,
      requiresConfirmation: false,
      followupId: null,
      draftStatus: null,
      extracted: null
    };
  }
}

export async function confirmCommunicationInput(params: {
  supabase: DbClient;
  profile: AuthLikeProfile;
  inputId: string;
  customerId?: string | null;
}): Promise<CaptureConfirmResult> {
  const { data, error } = await params.supabase
    .from("communication_inputs")
    .select("*")
    .eq("id", params.inputId)
    .eq("org_id", params.profile.org_id)
    .maybeSingle();

  if (error) throw new Error(error.message);
  if (!data) throw new Error("communication_input_not_found");

  const inputRow = data as CommunicationInputRow;
  if (inputRow.extraction_status === "failed") {
    return {
      inputId: inputRow.id,
      followupId: inputRow.extracted_followup_id,
      status: "skipped",
      message: "Extraction failed previously. Please create followup manually."
    };
  }

  const extractedData = (inputRow.extracted_data as Record<string, unknown> | null) ?? {};
  const extraction = readExtractionFromStoredData(extractedData);

  if (!extraction || !extraction.should_create_followup) {
    return {
      inputId: inputRow.id,
      followupId: inputRow.extracted_followup_id,
      status: "skipped",
      message: "No followup draft was generated from this input."
    };
  }

  let customer: CustomerRow | null = null;

  if (params.customerId) {
    const { data: chosen, error: customerError } = await params.supabase
      .from("customers")
      .select("*")
      .eq("id", params.customerId)
      .eq("org_id", params.profile.org_id)
      .maybeSingle();

    if (customerError) throw new Error(customerError.message);
    customer = (chosen ?? null) as CustomerRow | null;
  }

  if (!customer) {
    const matchedId = typeof extractedData?.match === "object" && extractedData.match && "customer_id" in extractedData.match
      ? (extractedData.match as { customer_id?: string | null }).customer_id
      : null;

    const fallbackCustomerId = params.customerId ?? matchedId ?? inputRow.customer_id;
    if (fallbackCustomerId) {
      const { data: fallbackCustomer } = await params.supabase
        .from("customers")
        .select("*")
        .eq("id", fallbackCustomerId)
        .eq("org_id", params.profile.org_id)
        .maybeSingle();
      customer = (fallbackCustomer ?? null) as CustomerRow | null;
    }
  }

  if (!customer) {
    return {
      inputId: inputRow.id,
      followupId: inputRow.extracted_followup_id,
      status: "skipped",
      message: "No matched customer. Please choose a customer before confirming."
    };
  }

  let followupId = inputRow.extracted_followup_id;

  if (followupId) {
    await params.supabase
      .from("followups")
      .update({
        draft_status: "confirmed"
      })
      .eq("id", followupId);
  } else {
    followupId = await createFollowupFromExtraction({
      supabase: params.supabase,
      orgId: params.profile.org_id,
      actorId: params.profile.id,
      customer,
      extraction,
      occurredAt: inputRow.occurred_at,
      sourceInputId: inputRow.id,
      draftStatus: "confirmed"
    });
  }

  await patchOpportunityFromExtraction({
    supabase: params.supabase,
    customerId: customer.id,
    extraction
  });

  const downstreamTrace = createEmptyCaptureTrace();

  try {
    const analysis = await runFollowupAnalysis({
      supabase: params.supabase,
      orgId: params.profile.org_id,
      customerId: customer.id,
      followupId,
      triggeredByUserId: params.profile.id,
      triggerSource: "manual"
    });
    downstreamTrace.followupAnalysisRunId = analysis.run.id;
    downstreamTrace.leakAlertAction = analysis.leakAlertAction;
    downstreamTrace.linkedWorkItemId = analysis.alertWorkItem?.workItemId ?? null;
    downstreamTrace.linkedWorkItemCreated = analysis.alertWorkItem?.created ?? null;
  } catch (cause) {
    downstreamTrace.downstreamErrors.push(cause instanceof Error ? cause.message : "followup_analysis_failed");
  }

  try {
    const eventResult = await upsertCaptureBusinessEvents({
      supabase: params.supabase,
      orgId: params.profile.org_id,
      input: {
        customerId: customer.id,
        ownerId: customer.owner_id,
        communicationInputId: inputRow.id,
        followupId,
        extraction,
        lifecycle: "capture_manual_confirmed"
      }
    });
    downstreamTrace.businessEventIds = eventResult.events.map((item) => item.id);
    downstreamTrace.businessEventCreatedCount = eventResult.createdCount;
    downstreamTrace.businessEventUpdatedCount = eventResult.updatedCount;
  } catch (cause) {
    downstreamTrace.downstreamErrors.push(cause instanceof Error ? cause.message : "capture_event_upsert_failed");
  }

  await params.supabase
    .from("communication_inputs")
    .update({
      customer_id: customer.id,
      extracted_followup_id: followupId,
      extraction_status: "completed",
      extraction_error: null,
      extracted_data: {
        ...extractedData,
        trace: {
          source_input_id: inputRow.id,
          followup_id: followupId,
          customer_id: customer.id,
          lifecycle: "capture_manual_confirmed",
          ...toCaptureTracePayload(downstreamTrace)
        }
      }
    })
    .eq("id", inputRow.id);

  return {
    inputId: inputRow.id,
    followupId,
    status: "confirmed",
    message:
      downstreamTrace.downstreamErrors.length > 0
        ? "Followup confirmed and linked to customer. Downstream analysis/events partially failed."
        : "Followup confirmed and linked to customer.",
    trace: downstreamTrace
  };
}
