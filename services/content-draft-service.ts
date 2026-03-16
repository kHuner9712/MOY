import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { buildFallbackActionDraft } from "@/lib/preparation-fallback";
import { deriveContentDraftStatusFromFeedback } from "@/lib/preparation-feedback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { checkOrgFeatureAccess } from "@/services/feature-access-service";
import { mapContentDraftRow } from "@/services/mappers";
import { addPrepFeedback } from "@/services/prep-card-service";
import { canRunAiByEntitlement, getEntitlementStatus } from "@/services/plan-entitlement-service";
import { getUserMemoryProfile } from "@/services/user-memory-service";
import { actionDraftGenerationResultSchema } from "@/types/ai";
import type { Database } from "@/types/database";
import type { ContentDraft, ContentDraftStatus, ContentDraftType, PrepFeedback } from "@/types/preparation";

type DbClient = ServerSupabaseClient;
type ProfileRow = Database["public"]["Tables"]["profiles"]["Row"];

function nowIso(): string {
  return new Date().toISOString();
}

export async function listContentDrafts(params: {
  supabase: DbClient;
  orgId: string;
  ownerId?: string;
  customerId?: string;
  workItemId?: string;
  limit?: number;
}): Promise<ContentDraft[]> {
  let query = params.supabase
    .from("content_drafts")
    .select("*")
    .eq("org_id", params.orgId)
    .order("created_at", { ascending: false })
    .limit(params.limit ?? 30);
  if (params.ownerId) query = query.eq("owner_id", params.ownerId);
  if (params.customerId) query = query.eq("customer_id", params.customerId);
  if (params.workItemId) query = query.eq("work_item_id", params.workItemId);

  const { data, error } = await query;
  if (error) throw new Error(error.message);
  const rows = (data ?? []) as Database["public"]["Tables"]["content_drafts"]["Row"][];
  return rows.map((item) => mapContentDraftRow(item));
}

export async function updateContentDraftStatus(params: {
  supabase: DbClient;
  orgId: string;
  draftId: string;
  status: ContentDraftStatus;
}): Promise<void> {
  const { error } = await params.supabase
    .from("content_drafts")
    .update({
      status: params.status,
      updated_at: nowIso()
    })
    .eq("org_id", params.orgId)
    .eq("id", params.draftId);
  if (error) throw new Error(error.message);
}

export async function generateContentDraft(params: {
  supabase: DbClient;
  profile: ProfileRow;
  draftType: ContentDraftType;
  customerId?: string | null;
  opportunityId?: string | null;
  prepCardId?: string | null;
  workItemId?: string | null;
  title?: string | null;
}): Promise<{ draft: ContentDraft; usedFallback: boolean; runId: string }> {
  const featureAccess = await checkOrgFeatureAccess({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    featureKey: "prep_cards"
  });
  if (!featureAccess.allowed) {
    throw new Error(featureAccess.reason ?? "prep_cards_feature_disabled");
  }

  const entitlement = await getEntitlementStatus({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    refreshUsage: true
  });
  const aiQuota = canRunAiByEntitlement(entitlement);
  if (!aiQuota.allowed) {
    throw new Error(aiQuota.reason ?? "ai_quota_reached");
  }

  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: true });
  const scenario = "action_draft_generation" as const;

  const workItemRes = params.workItemId
    ? await params.supabase
        .from("work_items")
        .select("id, owner_id, customer_id, title, rationale, work_type")
        .eq("org_id", params.profile.org_id)
        .eq("id", params.workItemId)
        .maybeSingle()
    : { data: null, error: null };
  if (workItemRes.error) throw new Error(workItemRes.error.message);

  const resolvedCustomerId = params.customerId ?? workItemRes.data?.customer_id ?? null;
  const ownerId = workItemRes.data?.owner_id ?? params.profile.id;

  const [customerRes, prepRes, memory] = await Promise.all([
    resolvedCustomerId
      ? params.supabase
          .from("customers")
          .select("id, company_name, contact_name, current_stage, risk_level, ai_summary, ai_suggestion")
          .eq("org_id", params.profile.org_id)
          .eq("id", resolvedCustomerId)
          .maybeSingle()
      : Promise.resolve({ data: null, error: null }),
    params.prepCardId
      ? params.supabase
          .from("prep_cards")
          .select("id, title, card_type, summary, card_payload")
          .eq("org_id", params.profile.org_id)
          .eq("id", params.prepCardId)
          .maybeSingle()
      : Promise.resolve({ data: null, error: null }),
    getUserMemoryProfile({
      supabase: params.supabase,
      orgId: params.profile.org_id,
      userId: ownerId
    }).catch(() => null)
  ]);
  if (customerRes.error) throw new Error(customerRes.error.message);
  if (prepRes.error) throw new Error(prepRes.error.message);

  const sourceSnapshot: Record<string, unknown> = {
    customer: customerRes.data ?? null,
    work_item: workItemRes.data ?? null,
    prep_card: prepRes.data ?? null,
    owner_memory_summary: memory?.summary ?? "",
    owner_memory_tactics: memory?.effectiveTactics ?? []
  };

  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    scenario,
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    customerId: resolvedCustomerId,
    followupId: null,
    triggeredByUserId: params.profile.id,
    triggerSource: "manual",
    scenario,
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    inputSnapshot: {
      draft_type: params.draftType,
      source_snapshot: sourceSnapshot
    }
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
  let result: {
    draft_title: string;
    content_text: string;
    content_markdown: string;
    rationale: string;
    caution_notes: string[];
  };
  let outputSnapshot: Record<string, unknown> = {};
  let responseProvider = provider.id;
  let responseModel = model;

  try {
    if (!provider.isConfigured()) throw new Error("provider_not_configured");
    const response = await provider.chatCompletion({
      scenario,
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        scenario,
        payload: {
          draft_type: params.draftType,
          source_snapshot: sourceSnapshot
        }
      }),
      jsonMode: true,
      strictMode: true,
      useReasonerModel: true
    });
    responseProvider = response.provider;
    responseModel = response.model;
    outputSnapshot = response.rawResponse;
    if (response.error) throw new Error(response.error);
    const parsed = actionDraftGenerationResultSchema.safeParse(response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null));
    if (!parsed.success) throw new Error("action_draft_generation_schema_invalid");
    result = parsed.data;
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "action_draft_generation_failed";
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
    fallbackReason = error instanceof Error ? error.message : "action_draft_generation_fallback";
    const fallback = buildFallbackActionDraft({
      draftType: params.draftType,
      customerName: customerRes.data?.company_name ?? null,
      taskTitle: workItemRes.data?.title ?? params.title ?? null
    });
    result = fallback;
    outputSnapshot = {
      fallback: true,
      reason: fallbackReason
    };
    responseModel = "rule-fallback";
  }

  const payload: Database["public"]["Tables"]["content_drafts"]["Insert"] = {
    org_id: params.profile.org_id,
    owner_id: ownerId,
    customer_id: resolvedCustomerId,
    opportunity_id: params.opportunityId ?? null,
    prep_card_id: params.prepCardId ?? null,
    work_item_id: params.workItemId ?? null,
    draft_type: params.draftType,
    status: "draft",
    title: params.title ?? result.draft_title,
    content_markdown: result.content_markdown,
    content_text: result.content_text,
    rationale: result.rationale,
    source_snapshot: {
      ...sourceSnapshot,
      caution_notes: result.caution_notes
    },
    generated_by: params.profile.id,
    ai_run_id: run.id
  };

  const { data: draftRaw, error: draftError } = await params.supabase.from("content_drafts").insert(payload).select("*").single();
  if (draftError || !draftRaw) throw new Error(draftError?.message ?? "Failed to save content draft");

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "completed",
    provider: responseProvider,
    model: responseModel,
    outputSnapshot,
    parsedResult: result,
    resultSource: usedFallback ? "fallback" : "provider",
    fallbackReason,
    latencyMs: Date.now() - startedAt,
    completedAt: nowIso(),
    errorMessage: usedFallback ? fallbackReason : null
  });

  return {
    draft: mapContentDraftRow(draftRaw as Database["public"]["Tables"]["content_drafts"]["Row"]),
    usedFallback,
    runId: run.id
  };
}

export async function addContentDraftFeedback(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  draftId: string;
  feedbackType: "useful" | "not_useful" | "adopted" | "inaccurate";
  feedbackText?: string | null;
}): Promise<PrepFeedback> {
  const nextStatus = deriveContentDraftStatusFromFeedback(params.feedbackType);
  if (nextStatus) {
    await updateContentDraftStatus({
      supabase: params.supabase,
      orgId: params.orgId,
      draftId: params.draftId,
      status: nextStatus
    });
  }

  return addPrepFeedback({
    supabase: params.supabase,
    orgId: params.orgId,
    userId: params.userId,
    targetType: "content_draft",
    targetId: params.draftId,
    feedbackType: params.feedbackType,
    feedbackText: params.feedbackText ?? null
  });
}
