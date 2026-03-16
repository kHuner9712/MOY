import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { pickLeadAssignmentRule } from "@/lib/commercialization";
import { buildFallbackLeadQualification } from "@/lib/commercialization-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import type { Database } from "@/types/database";
import { leadQualificationAssistResultSchema } from "@/types/ai";
import type { InboundLeadSource, LeadAssignmentRule, LeadQualificationAssistResult } from "@/types/commercialization";

type DbClient = ServerSupabaseClient;

interface LeadAssignmentRuleRow {
  id: string;
  org_id: string;
  rule_name: string;
  source_filter: string[] | null;
  industry_filter: string[] | null;
  team_size_filter: string[] | null;
  assign_to_user_id: string;
  priority: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

function nowIso(): string {
  return new Date().toISOString();
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function mapLeadAssignmentRuleRow(row: LeadAssignmentRuleRow): LeadAssignmentRule {
  return {
    id: row.id,
    orgId: row.org_id,
    ruleName: row.rule_name,
    sourceFilter: asStringArray(row.source_filter),
    industryFilter: asStringArray(row.industry_filter),
    teamSizeFilter: asStringArray(row.team_size_filter),
    assignToUserId: row.assign_to_user_id,
    priority: row.priority,
    isActive: row.is_active,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export async function listLeadAssignmentRules(params: {
  supabase: DbClient;
  orgId: string;
}): Promise<LeadAssignmentRule[]> {
  const res = await (params.supabase as any)
    .from("lead_assignment_rules")
    .select("*, assignee:profiles!lead_assignment_rules_assign_to_user_id_fkey(display_name)")
    .eq("org_id", params.orgId)
    .eq("is_active", true)
    .order("priority", { ascending: true })
    .order("created_at", { ascending: true });

  if (res.error) {
    if (res.error.message.includes("lead_assignment_rules")) return [];
    throw new Error(res.error.message);
  }

  return ((res.data ?? []) as Array<LeadAssignmentRuleRow & { assignee?: { display_name: string } | null }>).map((row) => ({
    ...mapLeadAssignmentRuleRow(row),
    assignToUserName: row.assignee?.display_name ?? undefined
  }));
}

async function listAssignableUsers(params: {
  supabase: DbClient;
  orgId: string;
}): Promise<Array<{ id: string; role: Database["public"]["Enums"]["org_member_role"]; displayName: string }>> {
  const res = await (params.supabase as any)
    .from("org_memberships")
    .select("user_id, role, seat_status, profile:profiles!org_memberships_user_id_fkey(display_name)")
    .eq("org_id", params.orgId)
    .eq("seat_status", "active")
    .in("role", ["owner", "admin", "manager", "sales"])
    .order("created_at", { ascending: true });

  if (res.error) throw new Error(res.error.message);

  return ((res.data ?? []) as Array<{ user_id: string; role: Database["public"]["Enums"]["org_member_role"]; profile?: { display_name: string } | null }>).map((row) => ({
    id: row.user_id,
    role: row.role,
    displayName: row.profile?.display_name ?? row.user_id
  }));
}

export async function resolveLeadOwner(params: {
  supabase: DbClient;
  orgId: string;
  leadSource: InboundLeadSource;
  industryHint: string | null;
  teamSizeHint: string | null;
}): Promise<{
  ownerId: string;
  ownerName: string;
  matchedRuleId: string | null;
}> {
  const [rules, users] = await Promise.all([
    listLeadAssignmentRules({
      supabase: params.supabase,
      orgId: params.orgId
    }),
    listAssignableUsers({
      supabase: params.supabase,
      orgId: params.orgId
    })
  ]);

  if (users.length === 0) throw new Error("lead_assignment_no_active_user");

  const matchedRule = pickLeadAssignmentRule({
    leadSource: params.leadSource,
    industryHint: params.industryHint,
    teamSizeHint: params.teamSizeHint,
    rules
  });

  if (matchedRule) {
    const user = users.find((item) => item.id === matchedRule.assignToUserId);
    if (user) {
      return {
        ownerId: user.id,
        ownerName: user.displayName,
        matchedRuleId: matchedRule.id
      };
    }
  }

  const salesFirst = users.find((item) => item.role === "sales");
  const managerFallback = users.find((item) => item.role === "manager" || item.role === "admin" || item.role === "owner");
  const chosen = salesFirst ?? managerFallback ?? users[0];

  return {
    ownerId: chosen.id,
    ownerName: chosen.displayName,
    matchedRuleId: null
  };
}

export async function runLeadQualificationAssist(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string | null;
  leadSource: InboundLeadSource;
  companyName: string;
  contactName: string;
  industryHint: string | null;
  teamSizeHint: string | null;
  useCaseHint: string | null;
}): Promise<{
  result: LeadQualificationAssistResult;
  runId: string;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const fallback = buildFallbackLeadQualification({
    leadSource: params.leadSource,
    industryHint: params.industryHint,
    teamSizeHint: params.teamSizeHint,
    useCaseHint: params.useCaseHint
  });

  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: false });
  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.orgId,
    scenario: "lead_qualification_assist",
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.orgId,
    triggerSource: "manual",
    scenario: "lead_qualification_assist",
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    triggeredByUserId: params.actorUserId,
    inputSnapshot: {
      lead_source: params.leadSource,
      company_name: params.companyName,
      contact_name: params.contactName,
      industry_hint: params.industryHint,
      team_size_hint: params.teamSizeHint,
      use_case_hint: params.useCaseHint
    }
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: nowIso()
  });

  let result = fallback;
  let usedFallback = false;
  let fallbackReason: string | null = null;
  let outputSnapshot: Record<string, unknown> = {
    fallback: true,
    reason: "not_started"
  };
  let responseModel = model;

  try {
    if (!provider.isConfigured()) throw new Error("provider_not_configured");

    const response = await provider.chatCompletion({
      scenario: "lead_qualification_assist",
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        lead_source: params.leadSource,
        company_name: params.companyName,
        contact_name: params.contactName,
        industry_hint: params.industryHint,
        team_size_hint: params.teamSizeHint,
        use_case_hint: params.useCaseHint
      }),
      jsonMode: true,
      strictMode: true
    });

    if (response.error) throw new Error(response.error);
    const candidate = response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null);
    const parsed = leadQualificationAssistResultSchema.safeParse(candidate);
    if (!parsed.success) throw new Error("lead_qualification_schema_invalid");

    result = {
      qualificationAssessment: parsed.data.qualification_assessment,
      fitScore: parsed.data.fit_score,
      likelyUseCase: parsed.data.likely_use_case,
      suggestedOwnerType: parsed.data.suggested_owner_type,
      suggestedNextActions: parsed.data.suggested_next_actions,
      riskFlags: parsed.data.risk_flags
    };
    outputSnapshot = response.rawResponse;
    responseModel = response.model;
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "lead_qualification_failed";
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
    fallbackReason = error instanceof Error ? error.message : "lead_qualification_fallback";
    outputSnapshot = {
      fallback: true,
      reason: fallbackReason,
      payload: result
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
    parsedResult: {
      qualification_assessment: result.qualificationAssessment,
      fit_score: result.fitScore,
      likely_use_case: result.likelyUseCase,
      suggested_owner_type: result.suggestedOwnerType,
      suggested_next_actions: result.suggestedNextActions,
      risk_flags: result.riskFlags
    },
    resultSource: usedFallback ? "fallback" : "provider",
    fallbackReason,
    errorMessage: usedFallback ? fallbackReason : null,
    completedAt: nowIso()
  });

  return {
    result,
    runId: run.id,
    usedFallback,
    fallbackReason
  };
}
