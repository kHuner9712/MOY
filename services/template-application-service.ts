import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { buildFallbackTemplateApplicationSummary } from "@/lib/productization-fallback";
import { applyTemplateConfig, type TemplateConfigDraft } from "@/lib/template-application";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import {
  getCurrentOrgTemplateContext,
  getIndustryTemplateDetail,
  upsertOrgTemplateOverride
} from "@/services/industry-template-service";
import { getOrgAiSettings, updateOrgAiSettings } from "@/services/org-ai-settings-service";
import { getOrgFeatureFlags, updateOrgFeatureFlag } from "@/services/org-feature-service";
import { runDemoSeed } from "@/services/demo-seed-service";
import { getOrgSettings, updateOrgSettings } from "@/services/org-settings-service";
import { seedPlaybooksFromTemplate } from "@/services/template-seed-service";
import { templateApplicationSummaryResultSchema } from "@/types/ai";
import type {
  OrgTemplateOverride,
  TemplateApplicationRun,
  TemplateApplicationSummary,
  TemplateApplyMode,
  TemplateApplyStrategy
} from "@/types/productization";

type DbClient = ServerSupabaseClient;

interface TemplateApplicationRunRow {
  id: string;
  org_id: string;
  template_id: string;
  initiated_by: string;
  run_type: "preview" | "apply" | "reapply" | "demo_seed_apply";
  apply_mode: TemplateApplyMode;
  apply_strategy: TemplateApplyStrategy;
  status: "queued" | "running" | "completed" | "failed";
  summary: string;
  diff_snapshot: Record<string, unknown> | null;
  result_snapshot: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

function nowIso(): string {
  return new Date().toISOString();
}

function asObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function asNumberRecord(value: unknown): Record<string, number> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const result: Record<string, number> = {};
  for (const [key, raw] of Object.entries(value as Record<string, unknown>)) {
    const num = Number(raw);
    if (Number.isFinite(num)) result[key] = num;
  }
  return result;
}

function mapRunRow(row: TemplateApplicationRunRow): TemplateApplicationRun {
  return {
    id: row.id,
    orgId: row.org_id,
    templateId: row.template_id,
    initiatedBy: row.initiated_by,
    runType: row.run_type,
    applyMode: row.apply_mode,
    applyStrategy: row.apply_strategy,
    status: row.status,
    summary: row.summary,
    diffSnapshot: asObject(row.diff_snapshot),
    resultSnapshot: asObject(row.result_snapshot),
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

function payloadToConfig(payload: Record<string, unknown>): TemplateConfigDraft {
  return {
    customerStages: asStringArray(payload.customer_stages),
    opportunityStages: asStringArray(payload.opportunity_stages),
    alertRules: asNumberRecord(payload.default_alert_rules),
    checkpoints: asStringArray(payload.suggested_checkpoints),
    managerAttentionSignals: asStringArray(payload.manager_attention_signals),
    prepPreferences: asStringArray(payload.prep_preferences),
    briefPreferences: asStringArray(payload.brief_preferences),
    recommendedOnboardingPath: asStringArray(payload.recommended_onboarding_path),
    demoSeedProfile: String(payload.demo_seed_profile ?? "generic_demo")
  };
}

async function ensurePersistentTemplateId(params: {
  supabase: DbClient;
  template: Awaited<ReturnType<typeof getIndustryTemplateDetail>>["template"];
}): Promise<string> {
  if (!params.template.id.startsWith("builtin:")) {
    return params.template.id;
  }

  const upsertRes = await (params.supabase as any)
    .from("industry_templates")
    .upsert(
      {
        template_key: params.template.templateKey,
        display_name: params.template.displayName,
        industry_family: params.template.industryFamily,
        status: params.template.status,
        summary: params.template.summary,
        template_payload: params.template.templatePayload,
        is_system_template: true
      },
      { onConflict: "template_key" }
    )
    .select("id")
    .single();

  if (upsertRes.error) throw new Error(upsertRes.error.message);
  return String(upsertRes.data.id);
}

function mergeOverrides(base: TemplateConfigDraft, overrides: OrgTemplateOverride[]): TemplateConfigDraft {
  const merged: TemplateConfigDraft = {
    ...base,
    customerStages: [...base.customerStages],
    opportunityStages: [...base.opportunityStages],
    alertRules: { ...base.alertRules },
    checkpoints: [...base.checkpoints],
    managerAttentionSignals: [...base.managerAttentionSignals],
    prepPreferences: [...base.prepPreferences],
    briefPreferences: [...base.briefPreferences],
    recommendedOnboardingPath: [...base.recommendedOnboardingPath]
  };

  for (const override of overrides) {
    const payload = override.overridePayload;
    if (override.overrideType === "customer_stages") merged.customerStages = asStringArray(payload.items ?? payload.customer_stages);
    if (override.overrideType === "opportunity_stages") merged.opportunityStages = asStringArray(payload.items ?? payload.opportunity_stages);
    if (override.overrideType === "alert_rules") merged.alertRules = { ...merged.alertRules, ...asNumberRecord(payload.rules ?? payload) };
    if (override.overrideType === "checkpoints") merged.checkpoints = asStringArray(payload.items ?? payload.checkpoints);
    if (override.overrideType === "prep_preferences") merged.prepPreferences = asStringArray(payload.items ?? payload.prep_preferences);
    if (override.overrideType === "brief_preferences") merged.briefPreferences = asStringArray(payload.items ?? payload.brief_preferences);
    if (override.overrideType === "demo_seed_profile") merged.demoSeedProfile = String(payload.value ?? payload.demo_seed_profile ?? merged.demoSeedProfile);
  }

  return merged;
}

async function createTemplateApplicationRun(params: {
  supabase: DbClient;
  orgId: string;
  templateId: string;
  actorUserId: string;
  runType: TemplateApplicationRun["runType"];
  applyMode: TemplateApplyMode;
  applyStrategy: TemplateApplyStrategy;
  status?: TemplateApplicationRun["status"];
  summary?: string;
  diffSnapshot?: Record<string, unknown>;
}): Promise<TemplateApplicationRun> {
  const res = await (params.supabase as any)
    .from("template_application_runs")
    .insert({
      org_id: params.orgId,
      template_id: params.templateId,
      initiated_by: params.actorUserId,
      run_type: params.runType,
      apply_mode: params.applyMode,
      apply_strategy: params.applyStrategy,
      status: params.status ?? "running",
      summary: params.summary ?? `${params.runType} started`,
      diff_snapshot: params.diffSnapshot ?? {},
      result_snapshot: {}
    })
    .select("*")
    .single();

  if (res.error) throw new Error(res.error.message);
  return mapRunRow(res.data as TemplateApplicationRunRow);
}

async function updateTemplateApplicationRun(params: {
  supabase: DbClient;
  runId: string;
  status: TemplateApplicationRun["status"];
  summary: string;
  resultSnapshot?: Record<string, unknown>;
  diffSnapshot?: Record<string, unknown>;
}): Promise<void> {
  const patch: Record<string, unknown> = {
    status: params.status,
    summary: params.summary
  };
  if (params.resultSnapshot !== undefined) patch.result_snapshot = params.resultSnapshot;
  if (params.diffSnapshot !== undefined) patch.diff_snapshot = params.diffSnapshot;
  const res = await (params.supabase as any).from("template_application_runs").update(patch).eq("id", params.runId);
  if (res.error) throw new Error(res.error.message);
}

async function generateApplicationSummary(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  templateId: string;
  applyMode: TemplateApplyMode;
  applyStrategy: TemplateApplyStrategy;
  diff: { changedKeys: string[]; unchangedKeys: string[]; notes: string[] };
  currentSettings: Record<string, unknown>;
  templatePayload: Record<string, unknown>;
}): Promise<{
  summary: TemplateApplicationSummary;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const scenario = "template_application_summary" as const;
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
      template_id: params.templateId,
      apply_mode: params.applyMode,
      apply_strategy: params.applyStrategy,
      diff: params.diff,
      current_settings: params.currentSettings,
      template_payload: params.templatePayload
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
        template_id: params.templateId,
        apply_mode: params.applyMode,
        apply_strategy: params.applyStrategy,
        diff: params.diff,
        current_settings: params.currentSettings,
        template_payload: params.templatePayload
      }),
      jsonMode: true,
      strictMode: true
    });

    const parsed = templateApplicationSummaryResultSchema.safeParse(
      response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null)
    );
    if (!parsed.success) throw new Error("template_application_summary_schema_invalid");

    const summary: TemplateApplicationSummary = {
      whatWillChange: parsed.data.what_will_change,
      whatWillNotChange: parsed.data.what_will_not_change,
      cautionNotes: parsed.data.caution_notes,
      recommendedNextSteps: parsed.data.recommended_next_steps
    };

    await updateAiRunStatus({
      supabase: params.supabase,
      runId: run.id,
      status: "completed",
      provider: response.provider,
      model: response.model,
      outputSnapshot: response.rawResponse,
      parsedResult: parsed.data,
      latencyMs: response.latencyMs,
      resultSource: "provider",
      completedAt: nowIso()
    });

    return { summary, usedFallback: false, fallbackReason: null };
  } catch (error) {
    const fallbackReason = error instanceof Error ? error.message : "template_application_summary_failed";
    if (!isRuleFallbackEnabled()) {
      await updateAiRunStatus({
        supabase: params.supabase,
        runId: run.id,
        status: "failed",
        errorMessage: fallbackReason,
        completedAt: nowIso()
      });
      throw error;
    }

    const fallback = buildFallbackTemplateApplicationSummary({
      changedKeys: params.diff.changedKeys,
      unchangedKeys: params.diff.unchangedKeys,
      strategy: params.applyStrategy
    });

    await updateAiRunStatus({
      supabase: params.supabase,
      runId: run.id,
      status: "completed",
      model: "rule-fallback",
      outputSnapshot: {
        fallback: true,
        reason: fallbackReason,
        payload: fallback
      },
      parsedResult: {
        what_will_change: fallback.whatWillChange,
        what_will_not_change: fallback.whatWillNotChange,
        caution_notes: fallback.cautionNotes,
        recommended_next_steps: fallback.recommendedNextSteps
      },
      resultSource: "fallback",
      fallbackReason,
      errorMessage: fallbackReason,
      completedAt: nowIso()
    });

    return { summary: fallback, usedFallback: true, fallbackReason };
  }
}

export async function previewTemplateApplication(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  templateIdOrKey: string;
  applyMode: TemplateApplyMode;
  applyStrategy: TemplateApplyStrategy;
}): Promise<{
  run: TemplateApplicationRun;
  diff: {
    changedKeys: string[];
    unchangedKeys: string[];
    notes: string[];
  };
  summary: TemplateApplicationSummary;
  summaryUsedFallback: boolean;
  summaryFallbackReason: string | null;
  template: Awaited<ReturnType<typeof getIndustryTemplateDetail>>["template"];
}> {
  const [settings, context, detail] = await Promise.all([
    getOrgSettings({ supabase: params.supabase, orgId: params.orgId }),
    getCurrentOrgTemplateContext({ supabase: params.supabase, orgId: params.orgId }),
    getIndustryTemplateDetail({
      supabase: params.supabase,
      templateIdOrKey: params.templateIdOrKey
    })
  ]);

  const existingConfig: TemplateConfigDraft = {
    customerStages: settings.defaultCustomerStages,
    opportunityStages: settings.defaultOpportunityStages,
    alertRules: settings.defaultAlertRules,
    checkpoints: asStringArray(context.template?.templatePayload.suggested_checkpoints),
    managerAttentionSignals: asStringArray(context.template?.templatePayload.manager_attention_signals),
    prepPreferences: asStringArray(context.template?.templatePayload.prep_preferences),
    briefPreferences: asStringArray(context.template?.templatePayload.brief_preferences),
    recommendedOnboardingPath: asStringArray(context.template?.templatePayload.recommended_onboarding_path),
    demoSeedProfile: String(context.template?.templatePayload.demo_seed_profile ?? "generic_demo")
  };

  const persistentTemplateId = await ensurePersistentTemplateId({
    supabase: params.supabase,
    template: detail.template
  });

  const incomingBase = payloadToConfig(detail.template.templatePayload);
  const incoming = mergeOverrides(
    incomingBase,
    context.overrides.filter((item) => item.templateId === detail.template.id || item.templateId === persistentTemplateId)
  );
  const applied = applyTemplateConfig({
    existing: existingConfig,
    incoming,
    strategy: params.applyStrategy
  });

  const run = await createTemplateApplicationRun({
    supabase: params.supabase,
    orgId: params.orgId,
    templateId: persistentTemplateId,
    actorUserId: params.actorUserId,
    runType: "preview",
    applyMode: params.applyMode,
    applyStrategy: params.applyStrategy,
    summary: "Template preview generated",
    diffSnapshot: applied.diff
  });

  const summaryResult = await generateApplicationSummary({
    supabase: params.supabase,
    orgId: params.orgId,
    actorUserId: params.actorUserId,
    templateId: persistentTemplateId,
    applyMode: params.applyMode,
    applyStrategy: params.applyStrategy,
    diff: applied.diff,
    currentSettings: {
      default_customer_stages: settings.defaultCustomerStages,
      default_opportunity_stages: settings.defaultOpportunityStages,
      default_alert_rules: settings.defaultAlertRules
    },
    templatePayload: detail.template.templatePayload
  });

  await updateTemplateApplicationRun({
    supabase: params.supabase,
    runId: run.id,
    status: "completed",
    summary: "Template preview completed",
    resultSnapshot: {
      summary: summaryResult.summary,
      summary_used_fallback: summaryResult.usedFallback,
      summary_fallback_reason: summaryResult.fallbackReason
    }
  });

  return {
    run: {
      ...run,
      status: "completed",
      summary: "Template preview completed",
      resultSnapshot: {
        summary: summaryResult.summary,
        summary_used_fallback: summaryResult.usedFallback,
        summary_fallback_reason: summaryResult.fallbackReason
      }
    },
    diff: applied.diff,
    summary: summaryResult.summary,
    summaryUsedFallback: summaryResult.usedFallback,
    summaryFallbackReason: summaryResult.fallbackReason,
    template: detail.template
  };
}

export async function applyTemplate(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  templateIdOrKey: string;
  applyMode: TemplateApplyMode;
  applyStrategy: TemplateApplyStrategy;
  generateDemoSeed?: boolean;
  overrides?: Array<{ overrideType: OrgTemplateOverride["overrideType"]; overridePayload: Record<string, unknown> }>;
}): Promise<{
  run: TemplateApplicationRun;
  appliedTemplateKey: string;
  playbookSeed: {
    createdCount: number;
    skippedCount: number;
    createdPlaybookIds: string[];
  };
  demoSeed: {
    executed: boolean;
    summary: string | null;
    partialSuccess: boolean;
  };
}> {
  const [settings, currentContext, detail] = await Promise.all([
    getOrgSettings({ supabase: params.supabase, orgId: params.orgId }),
    getCurrentOrgTemplateContext({ supabase: params.supabase, orgId: params.orgId }),
    getIndustryTemplateDetail({
      supabase: params.supabase,
      templateIdOrKey: params.templateIdOrKey
    })
  ]);
  const persistentTemplateId = await ensurePersistentTemplateId({
    supabase: params.supabase,
    template: detail.template
  });

  const existingConfig: TemplateConfigDraft = {
    customerStages: settings.defaultCustomerStages,
    opportunityStages: settings.defaultOpportunityStages,
    alertRules: settings.defaultAlertRules,
    checkpoints: asStringArray(currentContext.template?.templatePayload.suggested_checkpoints),
    managerAttentionSignals: asStringArray(currentContext.template?.templatePayload.manager_attention_signals),
    prepPreferences: asStringArray(currentContext.template?.templatePayload.prep_preferences),
    briefPreferences: asStringArray(currentContext.template?.templatePayload.brief_preferences),
    recommendedOnboardingPath: asStringArray(currentContext.template?.templatePayload.recommended_onboarding_path),
    demoSeedProfile: String(currentContext.template?.templatePayload.demo_seed_profile ?? "generic_demo")
  };

  const mergedOverrides = [...currentContext.overrides];
  if (params.overrides?.length) {
    for (const item of params.overrides) {
      const saved = await upsertOrgTemplateOverride({
        supabase: params.supabase,
        orgId: params.orgId,
        templateId: persistentTemplateId,
        overrideType: item.overrideType,
        overridePayload: item.overridePayload,
        actorUserId: params.actorUserId
      });
      const idx = mergedOverrides.findIndex((row) => row.overrideType === saved.overrideType && row.templateId === saved.templateId);
      if (idx >= 0) mergedOverrides[idx] = saved;
      else mergedOverrides.push(saved);
    }
  }

  const incomingBase = payloadToConfig(detail.template.templatePayload);
  const incoming = mergeOverrides(
    incomingBase,
    mergedOverrides.filter((item) => item.templateId === detail.template.id || item.templateId === persistentTemplateId)
  );
  const applied = applyTemplateConfig({
    existing: existingConfig,
    incoming,
    strategy: params.applyStrategy
  });

  const run = await createTemplateApplicationRun({
    supabase: params.supabase,
    orgId: params.orgId,
    templateId: persistentTemplateId,
    actorUserId: params.actorUserId,
    runType: params.generateDemoSeed ? "demo_seed_apply" : "apply",
    applyMode: params.applyMode,
    applyStrategy: params.applyStrategy,
    summary: "Template apply running",
    diffSnapshot: applied.diff
  });

  try {
    await updateOrgSettings({
      supabase: params.supabase,
      orgId: params.orgId,
      patch: {
        defaultCustomerStages: applied.merged.customerStages,
        defaultOpportunityStages: applied.merged.opportunityStages,
        defaultAlertRules: applied.merged.alertRules,
        industryHint: settings.industryHint ?? detail.template.displayName
      }
    });

    const [featureFlags, aiSettings] = await Promise.all([
      getOrgFeatureFlags({
        supabase: params.supabase,
        orgId: params.orgId
      }),
      getOrgAiSettings({
        supabase: params.supabase,
        orgId: params.orgId
      })
    ]);

    const payload = asObject(detail.template.templatePayload);
    const featureDefaults = asObject(payload.recommended_feature_flags);
    const aiDefaults = asObject(payload.recommended_ai_settings);

    for (const flag of featureFlags) {
      if (typeof featureDefaults[flag.featureKey] === "boolean") {
        await updateOrgFeatureFlag({
          supabase: params.supabase,
          orgId: params.orgId,
          featureKey: flag.featureKey,
          isEnabled: Boolean(featureDefaults[flag.featureKey]),
          configJson: flag.configJson
        });
      }
    }

    await updateOrgAiSettings({
      supabase: params.supabase,
      orgId: params.orgId,
      patch: {
        autoAnalysisEnabled: typeof aiDefaults.auto_analysis_enabled === "boolean" ? Boolean(aiDefaults.auto_analysis_enabled) : aiSettings.autoAnalysisEnabled,
        autoPlanEnabled: typeof aiDefaults.auto_plan_enabled === "boolean" ? Boolean(aiDefaults.auto_plan_enabled) : aiSettings.autoPlanEnabled,
        autoBriefEnabled: typeof aiDefaults.auto_brief_enabled === "boolean" ? Boolean(aiDefaults.auto_brief_enabled) : aiSettings.autoBriefEnabled
      }
    });

    await (params.supabase as any)
      .from("org_template_assignments")
      .update({
        assignment_status: "archived"
      })
      .eq("org_id", params.orgId)
      .eq("assignment_status", "active");

    const assignRes = await (params.supabase as any)
      .from("org_template_assignments")
      .insert({
        org_id: params.orgId,
        template_id: persistentTemplateId,
        assignment_status: "active",
        apply_mode: params.applyMode,
        apply_strategy: params.applyStrategy,
        applied_by: params.actorUserId
      })
      .select("id")
      .single();
    if (assignRes.error) throw new Error(assignRes.error.message);

    const playbookSeed = await seedPlaybooksFromTemplate({
      supabase: params.supabase,
      orgId: params.orgId,
      actorUserId: params.actorUserId,
      template: detail.template,
      seededTemplates: detail.seededPlaybookTemplates,
      applyMode: params.applyMode
    });

    let demoSeedResult: {
      executed: boolean;
      summary: string | null;
      partialSuccess: boolean;
    } = {
      executed: false,
      summary: null,
      partialSuccess: false
    };

    if (params.generateDemoSeed) {
      const seed = await runDemoSeed({
        supabase: params.supabase,
        orgId: params.orgId,
        actorUserId: params.actorUserId,
        runType: "demo_seed",
        templateKey: detail.template.templateKey
      });
      demoSeedResult = {
        executed: true,
        summary: seed.summary,
        partialSuccess: seed.partialSuccess
      };
    }

    const resultSnapshot = {
      applied_template_key: detail.template.templateKey,
      apply_mode: params.applyMode,
      apply_strategy: params.applyStrategy,
      changed_keys: applied.diff.changedKeys,
      playbook_seed: playbookSeed,
      demo_seed: demoSeedResult
    };

    await updateTemplateApplicationRun({
      supabase: params.supabase,
      runId: run.id,
      status: "completed",
      summary: `Template ${detail.template.displayName} applied`,
      resultSnapshot
    });

    return {
      run: {
        ...run,
        status: "completed",
        summary: `Template ${detail.template.displayName} applied`,
        resultSnapshot
      },
      appliedTemplateKey: detail.template.templateKey,
      playbookSeed,
      demoSeed: demoSeedResult
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "template_apply_failed";
    await updateTemplateApplicationRun({
      supabase: params.supabase,
      runId: run.id,
      status: "failed",
      summary: message,
      resultSnapshot: {
        error: message
      }
    }).catch(() => null);

    throw error;
  }
}

export async function listTemplateApplicationRuns(params: {
  supabase: DbClient;
  orgId: string;
  limit?: number;
}): Promise<TemplateApplicationRun[]> {
  const res = await (params.supabase as any)
    .from("template_application_runs")
    .select("*")
    .eq("org_id", params.orgId)
    .order("created_at", { ascending: false })
    .limit(params.limit ?? 20);
  if (res.error) throw new Error(res.error.message);
  return ((res.data ?? []) as TemplateApplicationRunRow[]).map(mapRunRow);
}
