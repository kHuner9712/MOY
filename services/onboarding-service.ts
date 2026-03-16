import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { buildFallbackOnboardingRecommendation } from "@/lib/productization-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { runDemoSeed } from "@/services/demo-seed-service";
import { getCurrentOrgMembership } from "@/services/org-membership-service";
import { getOrgAiControlStatus } from "@/services/org-ai-settings-service";
import { getOrgFeatureFlagMap } from "@/services/org-feature-service";
import { getCurrentOrgTemplateContext } from "@/services/industry-template-service";
import { getOrgSettings, patchOnboardingSteps } from "@/services/org-settings-service";
import { getEntitlementStatus } from "@/services/plan-entitlement-service";
import { mapOnboardingRunRow } from "@/services/mappers";
import { onboardingRecommendationResultSchema, type AiScenario } from "@/types/ai";
import type { Database, Json } from "@/types/database";
import type { OnboardingChecklist, OnboardingRecommendationResult, OnboardingRun } from "@/types/productization";

type DbClient = ServerSupabaseClient;
type OnboardingRunRow = Database["public"]["Tables"]["onboarding_runs"]["Row"];

function nowIso(): string {
  return new Date().toISOString();
}

async function createOnboardingRun(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  runType: Database["public"]["Enums"]["onboarding_run_type"];
  status?: Database["public"]["Enums"]["onboarding_run_status"];
  summary?: string;
  detailSnapshot?: Json;
}): Promise<OnboardingRun> {
  const res = await params.supabase
    .from("onboarding_runs")
    .insert({
      org_id: params.orgId,
      initiated_by: params.actorUserId,
      run_type: params.runType,
      status: params.status ?? "running",
      summary: params.summary ?? "onboarding run",
      detail_snapshot: params.detailSnapshot ?? {}
    })
    .select("*")
    .single();

  if (res.error) throw new Error(res.error.message);
  return mapOnboardingRunRow(res.data as OnboardingRunRow);
}

async function updateOnboardingRun(params: {
  supabase: DbClient;
  runId: string;
  status: Database["public"]["Enums"]["onboarding_run_status"];
  summary: string;
  detailSnapshot?: Json;
}): Promise<void> {
  const payload: Database["public"]["Tables"]["onboarding_runs"]["Update"] = {
    status: params.status,
    summary: params.summary
  };
  if (params.detailSnapshot !== undefined) payload.detail_snapshot = params.detailSnapshot;

  const res = await params.supabase.from("onboarding_runs").update(payload).eq("id", params.runId);
  if (res.error) throw new Error(res.error.message);
}

export async function buildOnboardingChecklist(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
}): Promise<OnboardingChecklist> {
  const [settings, aiStatus, featureFlags] = await Promise.all([
    getOrgSettings({ supabase: params.supabase, orgId: params.orgId }),
    getOrgAiControlStatus({ supabase: params.supabase, orgId: params.orgId }),
    getOrgFeatureFlagMap({ supabase: params.supabase, orgId: params.orgId })
  ]);

  const [memberCountRes, customerCountRes, planCountRes, briefCountRes, roomCountRes, managerCountRes, importCountRes, importedRowsRes, postImportOpsRes, templateCountRes] = await Promise.all([
    params.supabase.from("org_memberships").select("id", { count: "exact", head: true }).eq("org_id", params.orgId).eq("seat_status", "active"),
    params.supabase.from("customers").select("id", { count: "exact", head: true }).eq("org_id", params.orgId),
    params.supabase.from("daily_work_plans").select("id", { count: "exact", head: true }).eq("org_id", params.orgId),
    params.supabase.from("morning_briefs").select("id", { count: "exact", head: true }).eq("org_id", params.orgId),
    params.supabase.from("deal_rooms").select("id", { count: "exact", head: true }).eq("org_id", params.orgId),
    params.supabase.from("org_memberships").select("id", { count: "exact", head: true }).eq("org_id", params.orgId).in("role", ["manager", "admin", "owner"]).eq("seat_status", "active"),
    params.supabase.from("import_jobs").select("id", { count: "exact", head: true }).eq("org_id", params.orgId).in("import_type", ["customers", "mixed"]).eq("job_status", "completed"),
    params.supabase.from("import_jobs").select("imported_rows").eq("org_id", params.orgId).in("import_type", ["customers", "mixed"]).eq("job_status", "completed").order("created_at", { ascending: false }).limit(1).maybeSingle(),
    Promise.all([
      params.supabase.from("work_items").select("id", { count: "exact", head: true }).eq("org_id", params.orgId).limit(1),
      params.supabase.from("alerts").select("id", { count: "exact", head: true }).eq("org_id", params.orgId).limit(1),
      params.supabase.from("deal_rooms").select("id", { count: "exact", head: true }).eq("org_id", params.orgId).limit(1)
    ]),
    params.supabase.from("org_template_assignments").select("id", { count: "exact", head: true }).eq("org_id", params.orgId).eq("assignment_status", "active")
  ]);

  if (memberCountRes.error) throw new Error(memberCountRes.error.message);
  if (customerCountRes.error) throw new Error(customerCountRes.error.message);
  if (planCountRes.error) throw new Error(planCountRes.error.message);
  if (briefCountRes.error) throw new Error(briefCountRes.error.message);
  if (roomCountRes.error) throw new Error(roomCountRes.error.message);
  if (managerCountRes.error) throw new Error(managerCountRes.error.message);
  if (importCountRes.error) throw new Error(importCountRes.error.message);
  if (importedRowsRes.error) throw new Error(importedRowsRes.error.message);

  const [workItemCountRes, alertCountRes, dealRoomCountRes] = postImportOpsRes;
  if (templateCountRes.error) throw new Error(templateCountRes.error.message);
  if (workItemCountRes.error) throw new Error(workItemCountRes.error.message);
  if (alertCountRes.error) throw new Error(alertCountRes.error.message);
  if (dealRoomCountRes.error) throw new Error(dealRoomCountRes.error.message);

  const stepState = settings.onboardingStepState ?? {};
  const importedRows = (importedRowsRes.data as { imported_rows: number } | null)?.imported_rows ?? 0;
  const hasImportBootstrapSignals = (workItemCountRes.count ?? 0) > 0 && ((alertCountRes.count ?? 0) > 0 || (dealRoomCountRes.count ?? 0) > 0);
  const hasTemplateApplied = (templateCountRes.count ?? 0) > 0;

  const items = [
    {
      key: "industry_template",
      title: "Choose and apply industry template",
      completed: Boolean(stepState.industry_template) || hasTemplateApplied,
      detail: "Select one template to align stages, alerts and playbook seed with your industry motion."
    },
    {
      key: "org_profile",
      title: "Configure organization profile",
      completed: Boolean(stepState.org_profile) || !!settings.orgDisplayName,
      detail: "Set org display name, timezone and stage defaults."
    },
    {
      key: "ai_setup",
      title: "Configure AI provider",
      completed: Boolean(stepState.ai_setup) || aiStatus.providerConfigured,
      detail: "Ensure DeepSeek key and fallback policy are ready."
    },
    {
      key: "team_invite",
      title: "Invite 1-3 members",
      completed: Boolean(stepState.team_invite) || (memberCountRes.count ?? 0) >= 2,
      detail: "Activate at least one manager/admin and one sales rep."
    },
    {
      key: "first_data",
      title: "Create or import first customers",
      completed: Boolean(stepState.first_data) || (customerCountRes.count ?? 0) >= 3,
      detail: "Use capture/import/demo seed to bootstrap customer data."
    },
    {
      key: "import_first_batch",
      title: "Complete first customer import",
      completed: Boolean(stepState.import_first_batch) || (importCountRes.count ?? 0) > 0,
      detail: "Run at least one customers/mixed import job to map legacy data."
    },
    {
      key: "owner_mapping",
      title: "Verify owner mapping in imported data",
      completed: Boolean(stepState.owner_mapping) || importedRows > 0,
      detail: "Ensure import mapping can resolve owner names to active organization members."
    },
    {
      key: "post_import_bootstrap",
      title: "Generate post-import bootstrap actions",
      completed: Boolean(stepState.post_import_bootstrap) || hasImportBootstrapSignals,
      detail: "After import, generate first work items/alerts/deal room suggestions."
    },
    {
      key: "first_plan_or_brief",
      title: "Generate first daily plan or brief",
      completed: Boolean(stepState.first_plan_or_brief) || ((planCountRes.count ?? 0) > 0 || (briefCountRes.count ?? 0) > 0),
      detail: "Run /today plan generation or /briefings morning brief once."
    },
    {
      key: "first_deal_room",
      title: "Create first deal room",
      completed: Boolean(stepState.first_deal_room) || (roomCountRes.count ?? 0) > 0,
      detail: "Create at least one strategic deal room for manager collaboration."
    },
    {
      key: "manager_view",
      title: "Open manager execution view",
      completed: Boolean(stepState.manager_view) || (managerCountRes.count ?? 0) > 0,
      detail: "Use /manager, /manager/rhythm and /manager/outcomes at least once."
    }
  ];

  const completedCount = items.filter((item) => item.completed).length;
  const totalCount = items.length;
  const progress = totalCount === 0 ? 0 : Math.round((completedCount / totalCount) * 100);

  // keep step-state synced for later runs
  await patchOnboardingSteps({
    supabase: params.supabase,
    orgId: params.orgId,
    steps: Object.fromEntries(items.map((item) => [item.key, item.completed])),
    completeIfThreshold: true
  }).catch(() => null);

  if (!featureFlags.demo_seed_tools) {
    // no-op: only keeps lint from unused featureFlags and allows future conditional rendering
  }

  return {
    items,
    completedCount,
    totalCount,
    progress,
    completed: progress >= 72
  };
}

export async function generateOnboardingRecommendation(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  checklist: OnboardingChecklist;
}): Promise<{
  recommendation: OnboardingRecommendationResult;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const [settings, aiStatus, featureFlags] = await Promise.all([
    getOrgSettings({ supabase: params.supabase, orgId: params.orgId }),
    getOrgAiControlStatus({ supabase: params.supabase, orgId: params.orgId }),
    getOrgFeatureFlagMap({ supabase: params.supabase, orgId: params.orgId })
  ]);

  const scenario: AiScenario = "onboarding_recommendation";
  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: false });
  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.orgId,
    scenario,
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.orgId,
    triggerSource: "manual",
    scenario,
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    triggeredByUserId: params.actorUserId,
    inputSnapshot: {
      org_settings: settings,
      checklist: params.checklist,
      ai_status: aiStatus,
      feature_flags: featureFlags
    }
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: nowIso()
  });

  try {
    if (!provider.isConfigured()) throw new Error("provider_not_configured");

    const response = await provider.chatCompletion({
      scenario,
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        org_settings: settings,
        checklist: params.checklist,
        ai_status: aiStatus,
        feature_flags: featureFlags
      }),
      jsonMode: true,
      strictMode: true
    });

    const parsed = onboardingRecommendationResultSchema.safeParse(
      response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null)
    );

    if (!parsed.success) throw new Error("onboarding_recommendation_schema_invalid");

    await updateAiRunStatus({
      supabase: params.supabase,
      runId: run.id,
      status: "completed",
      provider: response.provider,
      model: response.model,
      outputSnapshot: response.rawResponse,
      parsedResult: parsed.data,
      errorMessage: response.error,
      latencyMs: response.latencyMs,
      resultSource: "provider",
      completedAt: nowIso()
    });

    return {
      recommendation: {
        nextBestSetupSteps: parsed.data.next_best_setup_steps,
        missingFoundations: parsed.data.missing_foundations,
        recommendedDemoFlow: parsed.data.recommended_demo_flow,
        recommendedTeamActions: parsed.data.recommended_team_actions,
        risksIfSkipped: parsed.data.risks_if_skipped
      },
      usedFallback: false,
      fallbackReason: null
    };
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "onboarding_recommendation_failed";
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

    const fallbackReason = error instanceof Error ? error.message : "onboarding_recommendation_fallback";
    const fallback = buildFallbackOnboardingRecommendation({
      checklist: params.checklist,
      featureFlags,
      hasAiConfigured: aiStatus.providerConfigured
    });

    await updateAiRunStatus({
      supabase: params.supabase,
      runId: run.id,
      status: "completed",
      provider: provider.id,
      model: "rule-fallback",
      outputSnapshot: {
        fallback: true,
        reason: fallbackReason,
        payload: fallback
      },
      parsedResult: {
        next_best_setup_steps: fallback.nextBestSetupSteps,
        missing_foundations: fallback.missingFoundations,
        recommended_demo_flow: fallback.recommendedDemoFlow,
        recommended_team_actions: fallback.recommendedTeamActions,
        risks_if_skipped: fallback.risksIfSkipped
      },
      errorMessage: fallbackReason,
      resultSource: "fallback",
      fallbackReason,
      completedAt: nowIso()
    });

    return {
      recommendation: fallback,
      usedFallback: true,
      fallbackReason
    };
  }
}

export async function getOnboardingOverview(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
}): Promise<{
  role: string;
  checklist: OnboardingChecklist;
  settings: Awaited<ReturnType<typeof getOrgSettings>>;
  aiStatus: Awaited<ReturnType<typeof getOrgAiControlStatus>>;
  featureFlags: Record<string, boolean>;
  entitlement: Awaited<ReturnType<typeof getEntitlementStatus>>;
  latestRuns: OnboardingRun[];
  currentTemplate: {
    templateKey: string;
    displayName: string;
  } | null;
  recommendation: OnboardingRecommendationResult;
  recommendationUsedFallback: boolean;
  recommendationFallbackReason: string | null;
}> {
  const [membership, settings, aiStatus, featureFlags, entitlement, checklistRes, runRes, templateContext] = await Promise.all([
    getCurrentOrgMembership({ supabase: params.supabase, orgId: params.orgId, userId: params.actorUserId }),
    getOrgSettings({ supabase: params.supabase, orgId: params.orgId }),
    getOrgAiControlStatus({ supabase: params.supabase, orgId: params.orgId }),
    getOrgFeatureFlagMap({ supabase: params.supabase, orgId: params.orgId }),
    getEntitlementStatus({ supabase: params.supabase, orgId: params.orgId, refreshUsage: true }),
    buildOnboardingChecklist({ supabase: params.supabase, orgId: params.orgId, actorUserId: params.actorUserId }),
    params.supabase.from("onboarding_runs").select("*").eq("org_id", params.orgId).order("created_at", { ascending: false }).limit(8),
    getCurrentOrgTemplateContext({ supabase: params.supabase, orgId: params.orgId })
  ]);

  if (runRes.error) throw new Error(runRes.error.message);

  const recommendationResult = await generateOnboardingRecommendation({
    supabase: params.supabase,
    orgId: params.orgId,
    actorUserId: params.actorUserId,
    checklist: checklistRes
  });

  return {
    role: membership?.role ?? "sales",
    checklist: checklistRes,
    settings,
    aiStatus,
    featureFlags,
    entitlement,
    latestRuns: ((runRes.data ?? []) as OnboardingRunRow[]).map((row) => mapOnboardingRunRow(row)),
    currentTemplate: templateContext.template
      ? {
          templateKey: templateContext.template.templateKey,
          displayName: templateContext.template.displayName
        }
      : null,
    recommendation: recommendationResult.recommendation,
    recommendationUsedFallback: recommendationResult.usedFallback,
    recommendationFallbackReason: recommendationResult.fallbackReason
  };
}

export async function runOnboardingFlow(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  runType: "first_time_setup" | "trial_bootstrap" | "demo_seed" | "reinitialize_demo";
}): Promise<{
  run: OnboardingRun;
  message: string;
  partialSuccess: boolean;
  detail: Record<string, unknown>;
}> {
  if (params.runType === "demo_seed" || params.runType === "reinitialize_demo") {
    const result = await runDemoSeed({
      supabase: params.supabase,
      orgId: params.orgId,
      actorUserId: params.actorUserId,
      runType: params.runType
    });

    await patchOnboardingSteps({
      supabase: params.supabase,
      orgId: params.orgId,
      steps: {
        first_data: result.inserted.customers > 0,
        first_plan_or_brief: result.inserted.work_items > 0,
        first_deal_room: result.inserted.deal_rooms > 0
      },
      completeIfThreshold: true
    }).catch(() => null);

    const runRes = await params.supabase.from("onboarding_runs").select("*").eq("id", result.runId).single();
    if (runRes.error) throw new Error(runRes.error.message);

    return {
      run: mapOnboardingRunRow(runRes.data as OnboardingRunRow),
      message: result.summary,
      partialSuccess: result.partialSuccess,
      detail: {
        steps: result.steps,
        inserted: result.inserted
      }
    };
  }

  const run = await createOnboardingRun({
    supabase: params.supabase,
    orgId: params.orgId,
    actorUserId: params.actorUserId,
    runType: params.runType,
    status: "running",
    summary: `Onboarding ${params.runType} started`
  });

  try {
    const checklist = await buildOnboardingChecklist({
      supabase: params.supabase,
      orgId: params.orgId,
      actorUserId: params.actorUserId
    });

    await patchOnboardingSteps({
      supabase: params.supabase,
      orgId: params.orgId,
      steps: Object.fromEntries(checklist.items.map((item) => [item.key, item.completed])),
      completeIfThreshold: true
    });

    await updateOnboardingRun({
      supabase: params.supabase,
      runId: run.id,
      status: "completed",
      summary: `Onboarding ${params.runType} completed`,
      detailSnapshot: {
        checklist
      } as unknown as Json
    });

    return {
      run: {
        ...run,
        status: "completed",
        summary: `Onboarding ${params.runType} completed`
      },
      message: `Onboarding ${params.runType} completed`,
      partialSuccess: false,
      detail: {
        checklist
      }
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "onboarding_run_failed";
    await updateOnboardingRun({
      supabase: params.supabase,
      runId: run.id,
      status: "failed",
      summary: message,
      detailSnapshot: {
        error: message
      } as Json
    }).catch(() => null);

    return {
      run: {
        ...run,
        status: "failed",
        summary: message
      },
      message,
      partialSuccess: true,
      detail: {
        error: message
      }
    };
  }
}
