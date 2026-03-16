import type { ZodSchema } from "zod";

import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { buildFallbackDocumentSummary, inferDocumentTypeFromName } from "@/lib/external-touchpoint-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { recordExternalTouchpointEvent, runTouchpointRules } from "@/services/external-touchpoint-service";
import { mapDocumentAssetRow } from "@/services/mappers";
import { createWorkItem } from "@/services/work-item-service";
import { documentAssetSummaryResultSchema, type AiScenario, type DocumentAssetSummaryResult } from "@/types/ai";
import type { Database } from "@/types/database";
import type { DocumentAsset } from "@/types/touchpoint";
import type { WorkItem } from "@/types/work";

type DbClient = ServerSupabaseClient;
type ProfileRow = Database["public"]["Tables"]["profiles"]["Row"];

interface ProfileLite {
  id: string;
  display_name: string;
}

interface CustomerLite {
  id: string;
  company_name: string;
}

function nowIso(): string {
  return new Date().toISOString();
}

function plusDays(days: number): string {
  const next = new Date();
  next.setDate(next.getDate() + days);
  return next.toISOString();
}

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

async function getDocumentAssetById(params: {
  supabase: DbClient;
  orgId: string;
  documentId: string;
}): Promise<DocumentAsset | null> {
  const { data, error } = await params.supabase
    .from("document_assets")
    .select("*, owner:profiles!document_assets_owner_id_fkey(id, display_name), customer:customers!document_assets_customer_id_fkey(id, company_name)")
    .eq("org_id", params.orgId)
    .eq("id", params.documentId)
    .maybeSingle();
  if (error) throw new Error(error.message);
  if (!data) return null;
  return mapDocumentAssetRow(
    data as Database["public"]["Tables"]["document_assets"]["Row"] & { owner?: ProfileLite | null; customer?: CustomerLite | null }
  );
}

async function ensureOpenWorkItemAbsent(params: {
  supabase: DbClient;
  orgId: string;
  sourceRefType: string;
  sourceRefId: string;
}): Promise<boolean> {
  const { data, error } = await params.supabase
    .from("work_items")
    .select("id")
    .eq("org_id", params.orgId)
    .eq("source_ref_type", params.sourceRefType)
    .eq("source_ref_id", params.sourceRefId)
    .in("status", ["todo", "in_progress", "snoozed"])
    .limit(1)
    .maybeSingle();
  if (error) throw new Error(error.message);
  return !data;
}

async function runTouchpointScenario<T>(params: {
  supabase: DbClient;
  profile: ProfileRow;
  scenario: AiScenario;
  customerId?: string | null;
  inputSnapshot: Record<string, unknown>;
  schema: ZodSchema<T>;
  fallbackBuilder: () => T;
}): Promise<{
  runId: string;
  result: T;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: true });

  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    scenario: params.scenario,
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    customerId: params.customerId ?? null,
    followupId: null,
    triggeredByUserId: params.profile.id,
    triggerSource: "manual",
    scenario: params.scenario,
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    inputSnapshot: params.inputSnapshot
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: nowIso()
  });

  const startedAt = Date.now();
  let usedFallback = false;
  let fallbackReason: string | null = null;
  let result: T;
  let outputSnapshot: Record<string, unknown> = {};
  let responseModel = model;

  try {
    if (!provider.isConfigured()) throw new Error("provider_not_configured");

    const response = await provider.chatCompletion({
      scenario: params.scenario,
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        scenario: params.scenario,
        payload: params.inputSnapshot
      }),
      jsonMode: true,
      strictMode: true,
      useReasonerModel: true
    });

    if (response.error) throw new Error(response.error);
    const candidate = response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null);
    const parsed = params.schema.safeParse(candidate);
    if (!parsed.success) throw new Error(`${params.scenario}_schema_invalid`);

    result = parsed.data;
    outputSnapshot = response.rawResponse;
    responseModel = response.model;
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : `${params.scenario}_failed`;
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
    fallbackReason = error instanceof Error ? error.message : `${params.scenario}_fallback`;
    result = params.fallbackBuilder();
    outputSnapshot = {
      fallback: true,
      reason: fallbackReason
    };
    responseModel = "rule-fallback";
  }

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "completed",
    provider: provider.id,
    model: responseModel,
    outputSnapshot,
    parsedResult: result as Record<string, unknown>,
    resultSource: usedFallback ? "fallback" : "provider",
    fallbackReason,
    latencyMs: Date.now() - startedAt,
    completedAt: nowIso(),
    errorMessage: usedFallback ? fallbackReason : null
  });

  console.info("[touchpoint.ai]", {
    org_id: params.profile.org_id,
    user_id: params.profile.id,
    customer_id: params.customerId ?? null,
    scenario: params.scenario,
    provider: provider.id,
    model: responseModel,
    status: "completed",
    duration_ms: Date.now() - startedAt,
    fallback_reason: fallbackReason
  });

  return {
    runId: run.id,
    result,
    usedFallback,
    fallbackReason
  };
}

export async function listDocumentAssets(params: {
  supabase: DbClient;
  orgId: string;
  ownerId?: string | null;
  customerId?: string | null;
  dealRoomId?: string | null;
  documentType?: Database["public"]["Enums"]["document_asset_type"];
  limit?: number;
}): Promise<DocumentAsset[]> {
  let query = params.supabase
    .from("document_assets")
    .select("*, owner:profiles!document_assets_owner_id_fkey(id, display_name), customer:customers!document_assets_customer_id_fkey(id, company_name)")
    .eq("org_id", params.orgId)
    .order("updated_at", { ascending: false })
    .limit(params.limit ?? 80);
  if (params.ownerId !== undefined) query = query.eq("owner_id", params.ownerId);
  if (params.customerId !== undefined) query = query.eq("customer_id", params.customerId);
  if (params.dealRoomId !== undefined) query = query.eq("deal_room_id", params.dealRoomId);
  if (params.documentType) query = query.eq("document_type", params.documentType);

  const { data, error } = await query;
  if (error) throw new Error(error.message);
  return (data ?? []).map(
    (row: Database["public"]["Tables"]["document_assets"]["Row"] & { owner?: ProfileLite | null; customer?: CustomerLite | null }) =>
      mapDocumentAssetRow(row)
  );
}

export async function uploadDocumentAsset(params: {
  supabase: DbClient;
  profile: ProfileRow;
  ownerId?: string;
  customerId?: string | null;
  opportunityId?: string | null;
  dealRoomId?: string | null;
  sourceType?: Database["public"]["Enums"]["document_asset_source_type"];
  documentType?: Database["public"]["Enums"]["document_asset_type"];
  title: string;
  fileName: string;
  mimeType?: string;
  storagePath?: string | null;
  extractedText?: string;
  tags?: string[];
  linkedPrepCardId?: string | null;
  linkedDraftId?: string | null;
  autoSummarize?: boolean;
}): Promise<{
  asset: DocumentAsset;
  summaryResult: DocumentAssetSummaryResult | null;
  summaryRunId: string | null;
  usedFallback: boolean;
  fallbackReason: string | null;
  linkedWorkItems: WorkItem[];
}> {
  const guessedType = inferDocumentTypeFromName(params.fileName);
  const payload: Database["public"]["Tables"]["document_assets"]["Insert"] = {
    org_id: params.profile.org_id,
    owner_id: params.ownerId ?? params.profile.id,
    customer_id: params.customerId ?? null,
    opportunity_id: params.opportunityId ?? null,
    deal_room_id: params.dealRoomId ?? null,
    source_type: params.sourceType ?? "upload",
    document_type: params.documentType ?? guessedType,
    title: params.title,
    file_name: params.fileName,
    mime_type: params.mimeType ?? "text/plain",
    storage_path: params.storagePath ?? null,
    extracted_text: params.extractedText ?? "",
    summary: "",
    tags: (params.tags ?? []) as Database["public"]["Tables"]["document_assets"]["Insert"]["tags"],
    linked_prep_card_id: params.linkedPrepCardId ?? null,
    linked_draft_id: params.linkedDraftId ?? null
  };

  const { data, error } = await params.supabase
    .from("document_assets")
    .insert(payload)
    .select("*, owner:profiles!document_assets_owner_id_fkey(id, display_name), customer:customers!document_assets_customer_id_fkey(id, company_name)")
    .single();
  if (error || !data) throw new Error(error?.message ?? "upload_document_asset_failed");

  const asset = mapDocumentAssetRow(
    data as Database["public"]["Tables"]["document_assets"]["Row"] & { owner?: ProfileLite | null; customer?: CustomerLite | null }
  );

  await recordExternalTouchpointEvent({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    ownerId: asset.ownerId,
    customerId: asset.customerId,
    opportunityId: asset.opportunityId,
    dealRoomId: asset.dealRoomId,
    touchpointType: "document",
    eventType: "document_uploaded",
    relatedRefType: "document_asset",
    relatedRefId: asset.id,
    eventSummary: `Document uploaded: ${asset.title}`,
    eventPayload: {
      document_type: asset.documentType,
      file_name: asset.fileName
    }
  });

  let summaryResult: DocumentAssetSummaryResult | null = null;
  let summaryRunId: string | null = null;
  let usedFallback = false;
  let fallbackReason: string | null = null;
  let linkedWorkItems: WorkItem[] = [];

  if (params.autoSummarize ?? true) {
    const summarized = await summarizeDocumentAsset({
      supabase: params.supabase,
      profile: params.profile,
      documentId: asset.id
    });
    summaryResult = summarized.result;
    summaryRunId = summarized.runId;
    usedFallback = summarized.usedFallback;
    fallbackReason = summarized.fallbackReason;
    linkedWorkItems = summarized.linkedWorkItems;
    return {
      asset: summarized.asset,
      summaryResult,
      summaryRunId,
      usedFallback,
      fallbackReason,
      linkedWorkItems
    };
  }

  await runTouchpointRules({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    actorUserId: params.profile.id
  }).catch(() => null);

  return {
    asset,
    summaryResult,
    summaryRunId,
    usedFallback,
    fallbackReason,
    linkedWorkItems
  };
}

export async function summarizeDocumentAsset(params: {
  supabase: DbClient;
  profile: ProfileRow;
  documentId: string;
}): Promise<{
  asset: DocumentAsset;
  result: DocumentAssetSummaryResult;
  runId: string;
  usedFallback: boolean;
  fallbackReason: string | null;
  linkedWorkItems: WorkItem[];
}> {
  const asset = await getDocumentAssetById({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    documentId: params.documentId
  });
  if (!asset) throw new Error("document_asset_not_found");

  const [customerRes, roomRes] = await Promise.all([
    asset.customerId
      ? params.supabase
          .from("customers")
          .select("id, company_name, current_stage, risk_level, win_probability")
          .eq("org_id", params.profile.org_id)
          .eq("id", asset.customerId)
          .maybeSingle()
      : Promise.resolve({ data: null, error: null }),
    asset.dealRoomId
      ? params.supabase
          .from("deal_rooms")
          .select("id, title, room_status, current_goal, manager_attention_needed")
          .eq("org_id", params.profile.org_id)
          .eq("id", asset.dealRoomId)
          .maybeSingle()
      : Promise.resolve({ data: null, error: null })
  ]);
  if (customerRes.error) throw new Error(customerRes.error.message);
  if (roomRes.error) throw new Error(roomRes.error.message);

  const inputSnapshot = {
    document: {
      id: asset.id,
      title: asset.title,
      file_name: asset.fileName,
      mime_type: asset.mimeType,
      current_document_type: asset.documentType,
      extracted_text: asset.extractedText.slice(0, 12000)
    },
    customer: customerRes.data ?? null,
    deal_room: roomRes.data ?? null
  } as Record<string, unknown>;

  const execution = await runTouchpointScenario({
    supabase: params.supabase,
    profile: params.profile,
    scenario: "document_asset_summary",
    customerId: asset.customerId,
    inputSnapshot,
    schema: documentAssetSummaryResultSchema,
    fallbackBuilder: () =>
      buildFallbackDocumentSummary({
        fileName: asset.fileName,
        extractedText: asset.extractedText
      })
  });
  const normalizedResult: DocumentAssetSummaryResult = {
    document_type_guess: execution.result.document_type_guess,
    summary: execution.result.summary,
    risk_flags: execution.result.risk_flags ?? [],
    recommended_actions: execution.result.recommended_actions ?? [],
    related_checkpoint_hint: execution.result.related_checkpoint_hint ?? []
  };

  const tags = [
    ...toStringArray(asset.tags),
    normalizedResult.document_type_guess,
    ...normalizedResult.risk_flags.map((item) => item.toLowerCase().slice(0, 60))
  ]
    .filter(Boolean)
    .slice(0, 12);

  const { error: updateError } = await params.supabase
    .from("document_assets")
    .update({
      document_type: normalizedResult.document_type_guess,
      summary: normalizedResult.summary,
      tags: tags as Database["public"]["Tables"]["document_assets"]["Update"]["tags"],
      updated_at: nowIso()
    })
    .eq("org_id", params.profile.org_id)
    .eq("id", asset.id);
  if (updateError) throw new Error(updateError.message);

  await recordExternalTouchpointEvent({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    ownerId: asset.ownerId,
    customerId: asset.customerId,
    opportunityId: asset.opportunityId,
    dealRoomId: asset.dealRoomId,
    touchpointType: "document",
    eventType: "attachment_extracted",
    relatedRefType: "document_asset",
    relatedRefId: asset.id,
    eventSummary: `Document summarized: ${asset.title}`,
    eventPayload: normalizedResult
  });

  const linkedWorkItems: WorkItem[] = [];
  if (asset.customerId) {
    if (normalizedResult.document_type_guess === "quote") {
      const canCreate = await ensureOpenWorkItemAbsent({
        supabase: params.supabase,
        orgId: params.profile.org_id,
        sourceRefType: "document_quote_followup",
        sourceRefId: asset.id
      });
      if (canCreate) {
        linkedWorkItems.push(
          await createWorkItem({
            supabase: params.supabase,
            orgId: params.profile.org_id,
            ownerId: asset.ownerId,
            customerId: asset.customerId,
            opportunityId: asset.opportunityId,
            sourceType: "manual",
            workType: "send_quote",
            title: `[Document] Quote follow-up: ${asset.title}`,
            description: normalizedResult.summary,
            rationale: "Quote-like document uploaded. Follow up with customer feedback and next step.",
            priorityScore: 78,
            priorityBand: "high",
            dueAt: plusDays(1),
            sourceRefType: "document_quote_followup",
            sourceRefId: asset.id,
            createdBy: params.profile.id
          })
        );
      }
    }

    if (normalizedResult.document_type_guess === "contract_draft") {
      const canCreate = await ensureOpenWorkItemAbsent({
        supabase: params.supabase,
        orgId: params.profile.org_id,
        sourceRefType: "document_contract_review",
        sourceRefId: asset.id
      });
      if (canCreate) {
        linkedWorkItems.push(
          await createWorkItem({
            supabase: params.supabase,
            orgId: params.profile.org_id,
            ownerId: asset.ownerId,
            customerId: asset.customerId,
            opportunityId: asset.opportunityId,
            sourceType: "manual",
            workType: "manager_checkin",
            title: `[Document] Contract review support: ${asset.title}`,
            description: normalizedResult.summary,
            rationale: "Contract-draft signal detected; manager/support alignment is recommended.",
            priorityScore: 84,
            priorityBand: "high",
            dueAt: plusDays(1),
            sourceRefType: "document_contract_review",
            sourceRefId: asset.id,
            createdBy: params.profile.id
          })
        );
      }
    }
  }

  await runTouchpointRules({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    actorUserId: params.profile.id
  }).catch(() => null);

  const refreshed = await getDocumentAssetById({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    documentId: asset.id
  });
  if (!refreshed) throw new Error("document_asset_not_found_after_summary");

  return {
    asset: refreshed,
    result: normalizedResult,
    runId: execution.runId,
    usedFallback: execution.usedFallback,
    fallbackReason: execution.fallbackReason,
    linkedWorkItems
  };
}
