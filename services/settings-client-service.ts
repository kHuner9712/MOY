import type {
  EntitlementStatus,
  IndustryTemplate,
  IndustryTemplateContext,
  OrgTemplateOverride,
  TemplateApplicationRun,
  TemplateApplyMode,
  TemplateApplyStrategy,
  TemplateFitRecommendation,
  TemplateApplicationSummary,
  OnboardingChecklist,
  OnboardingRecommendationResult,
  OnboardingRun,
  OrgAiSettings,
  OrgFeatureFlag,
  OrgFeatureKey,
  OrgInvite,
  OrgMembership,
  OrgSettings,
  OrgUsageCounter,
  UserUsageCounter
} from "@/types/productization";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

interface ApiPayloadWithStatus<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

export interface TemplateOverrideExpectedVersion {
  compareToken?: string | null;
  versionLabel?: string | null;
  versionNumber?: number | null;
  overrideUpdatedAt?: string | null;
  payloadHash?: string | null;
}

export interface TemplateOverrideConflictPayload {
  conflict: true;
  conflictReason: string | null;
  currentVersion: Record<string, unknown> | null;
  expectedVersion: Record<string, unknown> | null;
  diagnostics: string[];
}

export interface TemplateOverrideEditorStatePayload {
  role: string;
  state: {
    templateId: string;
    overrideType: OrgTemplateOverride["overrideType"];
    currentOverride: {
      exists: boolean;
      id: string | null;
      createdBy: string | null;
      createdAt: string | null;
      updatedAt: string | null;
      payload: Record<string, unknown>;
    };
    currentDiagnostics: Record<string, unknown> | null;
    concurrencyBaseline: Record<string, unknown>;
    expectedVersion: TemplateOverrideExpectedVersion;
    latestPersistedVersion: {
      availability: "available" | "empty" | "not_available";
      versionLabel: string | null;
      versionNumber: number | null;
      note: string;
    };
    recentAudits: {
      availability: "available" | "empty" | "not_available";
      note: string;
      items: Array<{
        id: string;
        createdAt: string;
        actorUserId: string;
        actionType: string;
        versionLabel: string;
        versionNumber: number;
        runtimeImpactSummary: string | null;
        diagnosticsPreview: string[];
      }>;
    };
  };
}

export interface TemplateOverrideWritePreviewPayload {
  role: string;
  preview: {
    templateId: string;
    overrideType: OrgTemplateOverride["overrideType"];
    writeDiagnostics: Record<string, unknown>;
    diagnosticsSummary: Record<string, unknown>;
  };
}

export interface TemplateOverrideWriteSuccessPayload {
  override: OrgTemplateOverride;
  writeDiagnostics: Record<string, unknown>;
  diagnosticsSummary: Record<string, unknown>;
  auditDraft: Record<string, unknown>;
  concurrency: {
    expectedVersion: Record<string, unknown> | null;
    beforeWrite: Record<string, unknown>;
    afterWrite: Record<string, unknown>;
  };
  persistedAudit: {
    status: "persisted" | "not_available";
    record: Record<string, unknown> | null;
    reason: string | null;
  };
}

export interface TemplateOverrideRollbackPreviewPayload {
  role: string;
  preview: {
    generatedAt: string;
    status: "allowed" | "rejected" | "not_available";
    canExecute: boolean;
    reason: string | null;
    diagnostics: string[];
    request: {
      orgId: string;
      templateId: string;
      overrideType: OrgTemplateOverride["overrideType"];
    };
    targetVersion: {
      auditId: string | null;
      versionLabel: string | null;
      versionNumber: number | null;
      actionType: string | null;
      createdAt: string | null;
    };
    currentValue: {
      exists: boolean;
      targetId: string | null;
      summary: Record<string, unknown> | null;
    };
    targetValue: {
      summary: Record<string, unknown> | null;
      normalizedPayload: Record<string, unknown>;
    };
    restorePlan: {
      acceptedFields: string[];
      ignoredFields: string[];
      runtimeImpactSummary: string;
      acceptedForRuntime: boolean;
      forbiddenForRuntime: boolean;
    };
    concurrency: {
      baseline: Record<string, unknown> | null;
      expectedVersion: TemplateOverrideExpectedVersion | null;
      note: string;
    };
  };
}

export interface TemplateOverrideRollbackExecuteSuccessPayload {
  role: string;
  execution: Record<string, unknown>;
}

export type OrgConfigEditorTargetType =
  | "org_settings"
  | "org_ai_settings"
  | "org_feature_flags";

export interface OrgConfigExpectedVersion {
  compareToken?: string | null;
  versionLabel?: string | null;
  versionNumber?: number | null;
  overrideUpdatedAt?: string | null;
  payloadHash?: string | null;
}

export interface OrgConfigConflictPayload {
  conflict: true;
  conflictReason: string | null;
  currentVersion: Record<string, unknown> | null;
  expectedVersion: Record<string, unknown> | null;
  diagnostics: string[];
}

export interface OrgConfigEditorStatePayload {
  role: string;
  canManage: boolean;
  state: {
    generatedAt: string;
    sections: {
      orgSettings: {
        targetType: "org_settings";
        expectedVersion: OrgConfigExpectedVersion;
        concurrencyBaseline: Record<string, unknown>;
        latestPersistedVersion: {
          availability: "available" | "empty" | "not_available";
          versionLabel: string | null;
          versionNumber: number | null;
          note: string;
        };
        recentAudits: {
          availability: "available" | "empty" | "not_available";
          note: string;
          items: Array<{
            id: string;
            actionType: string;
            versionLabel: string;
            versionNumber: number;
            createdAt: string;
            actorUserId: string;
            diagnosticsPreview: string[];
            runtimeImpactSummary: string | null;
          }>;
        };
        latestDiagnosticsSummary: {
          diagnostics: string[];
          acceptedFields: string[];
          ignoredFields: string[];
          forbiddenFields: string[];
          runtimeImpactSummary: string | null;
        } | null;
        currentValue: Record<string, unknown>;
      };
      orgAiSettings: {
        targetType: "org_ai_settings";
        expectedVersion: OrgConfigExpectedVersion;
        concurrencyBaseline: Record<string, unknown>;
        latestPersistedVersion: {
          availability: "available" | "empty" | "not_available";
          versionLabel: string | null;
          versionNumber: number | null;
          note: string;
        };
        recentAudits: {
          availability: "available" | "empty" | "not_available";
          note: string;
          items: Array<{
            id: string;
            actionType: string;
            versionLabel: string;
            versionNumber: number;
            createdAt: string;
            actorUserId: string;
            diagnosticsPreview: string[];
            runtimeImpactSummary: string | null;
          }>;
        };
        latestDiagnosticsSummary: {
          diagnostics: string[];
          acceptedFields: string[];
          ignoredFields: string[];
          forbiddenFields: string[];
          runtimeImpactSummary: string | null;
        } | null;
        currentValue: Record<string, unknown>;
      };
      orgFeatureFlags: {
        targetType: "org_feature_flags";
        expectedVersion: OrgConfigExpectedVersion;
        concurrencyBaseline: Record<string, unknown>;
        latestPersistedVersion: {
          availability: "available" | "empty" | "not_available";
          versionLabel: string | null;
          versionNumber: number | null;
          note: string;
        };
        recentAudits: {
          availability: "available" | "empty" | "not_available";
          note: string;
          items: Array<{
            id: string;
            actionType: string;
            versionLabel: string;
            versionNumber: number;
            createdAt: string;
            actorUserId: string;
            diagnosticsPreview: string[];
            runtimeImpactSummary: string | null;
          }>;
        };
        latestDiagnosticsSummary: {
          diagnostics: string[];
          acceptedFields: string[];
          ignoredFields: string[];
          forbiddenFields: string[];
          runtimeImpactSummary: string | null;
        } | null;
        currentValue: Record<string, unknown>;
      };
    };
  };
}

export interface OrgConfigWritePreviewPayload {
  role: string;
  preview: {
    targetType: OrgConfigEditorTargetType;
    writeDiagnostics: Record<string, unknown>;
    diagnosticsSummary: Record<string, unknown>;
  };
}

export interface OrgConfigWriteSuccessPayload {
  targetType: OrgConfigEditorTargetType;
  writeDiagnostics: Record<string, unknown>;
  auditDraft: Record<string, unknown>;
  persistedAudit: {
    status: "persisted" | "not_available";
    record: Record<string, unknown> | null;
    reason: string | null;
  };
  concurrency: {
    expectedVersion: Record<string, unknown> | null;
    beforeWrite: Record<string, unknown>;
    afterWrite: Record<string, unknown>;
  };
}

export interface OrgConfigRollbackPreviewPayload {
  role: string;
  preview: {
    generatedAt: string;
    targetType: OrgConfigEditorTargetType;
    status: "allowed" | "rejected" | "not_available";
    canExecute: boolean;
    reason: string | null;
    diagnostics: string[];
    request: {
      orgId: string;
      targetType: OrgConfigEditorTargetType;
      targetKey: string;
    };
    targetVersion: {
      auditId: string | null;
      versionLabel: string | null;
      versionNumber: number | null;
      actionType: string | null;
      createdAt: string | null;
    };
    currentValue: {
      summary: Record<string, unknown> | null;
    };
    targetValue: {
      summary: Record<string, unknown> | null;
      restoredSummary: Record<string, unknown> | null;
      normalizedPatch: Record<string, unknown>;
    };
    restorePlan: {
      acceptedFields: string[];
      ignoredFields: string[];
      forbiddenFields: string[];
      runtimeImpactSummary: string;
    };
    concurrency: {
      baseline: Record<string, unknown> | null;
      expectedVersion: OrgConfigExpectedVersion | null;
      note: string;
    };
  };
}

export interface OrgConfigRollbackExecuteSuccessPayload {
  role: string;
  execution: Record<string, unknown>;
}

export interface RuntimeExplainDebugPanelPayload {
  generatedAt: string;
  runtime: {
    resolvedTemplateKey: string | null;
    fallbackProfileKey: string;
    appliedOrgCustomizationKey: string;
    resolvedMode: "seed_only" | "persisted_preferred";
    sourcePriority: string[];
    keyFieldSources: Record<string, string>;
    persistedUsage: Record<string, boolean>;
    appliedOverrides: Array<{
      overrideType: string;
      layer: string;
      appliedFields: string[];
    }>;
    ignoredOverrides: Array<{
      overrideType: string;
      layer: string;
      reason: string;
      diagnostics: string[];
    }>;
    diagnostics: string[];
  };
  effectivePreferenceSummary: {
    managerFocusMetrics: string[];
    reportMetricFilters: string[];
    executiveMetricFilters: string[];
    recommendedActionTitles: string[];
    onboardingPreferredChecklistKeys: string[];
    onboardingHints: string[];
    defaultDateRangeDays: number | null;
  };
  consumerExplainSummary: {
    onboarding: {
      promptAugmentationEnabled: boolean;
      promptAugmentationPreview: string | null;
      preferredChecklistKeys: string[];
      hintPreview: string[];
      explainSource: string;
    };
    automationSeed: {
      resolutionSource: string;
      resolvedMode: "seed_only" | "persisted_preferred";
      ignoredOverrideCount: number;
      totalSeedCount: number;
      disabledSeedCount: number;
      sample: Array<{
        ruleKey: string;
        isEnabled: boolean;
        conditionKeys: string[];
      }>;
    };
    executiveReport: {
      fallbackToBase: boolean;
      managerFocusMetricPriority: string[];
      reportMetricPriority: string[];
      recommendedActionPriority: string[];
      defaultDateRangeDays: number | null;
    };
  };
  overrideWriteGovernance: {
    availability: "available_from_template_apply_snapshot" | "not_available";
    latestRunId: string | null;
    latestRunAt: string | null;
    summary: Record<string, unknown> | null;
    diagnosticsCount: number;
    auditDraftCount: number;
    note: string;
  };
  recentPersistedAudits: {
    availability: "available" | "empty" | "not_available";
    note: string;
    items: Array<{
      id: string;
      createdAt: string;
      actorUserId: string;
      targetType: string;
      targetKey: string | null;
      actionType: string;
      versionLabel: string;
      versionNumber: number;
      runtimeImpactSummary: string | null;
      forbiddenForRuntime: boolean | null;
      ignoredByRuntime: boolean | null;
      diagnosticsPreview: string[];
      hasConcurrencyConflictDiagnostic: boolean;
    }>;
  };
  recentRollbackAudits: {
    availability: "available" | "empty" | "not_available";
    note: string;
    items: Array<{
      id: string;
      createdAt: string;
      actorUserId: string;
      targetType: string;
      targetKey: string | null;
      versionLabel: string;
      versionNumber: number;
      restoredFromAuditId: string | null;
      restoredFromVersionLabel: string | null;
      restoredFromVersionNumber: number | null;
      diagnosticsPreview: string[];
      hasConcurrencyConflictDiagnostic: boolean;
    }>;
  };
  concurrencyGuard: {
    latestConflictReason: string | null;
    latestConflictAt: string | null;
    note: string;
  };
  orgConfigGovernance: {
    note: string;
    items: Array<{
      targetType: "org_settings" | "org_ai_settings" | "org_feature_flags";
      availability: "available" | "empty" | "not_available";
      hasPersistedAudit: boolean;
      latestChangedAt: string | null;
      latestActionType: string | null;
      latestVersionLabel: string | null;
      latestVersionNumber: number | null;
      runtimeImpactSummary: string | null;
      diagnosticsPreview: string[];
      ignoredOrForbiddenDiagnosticsCount: number;
      conflictDiagnosticsCount: number;
      note: string;
    }>;
  };
}

export interface ConfigOperationsHubPayload {
  generatedAt: string;
  runtimeOverview: {
    resolvedTemplateKey: string | null;
    fallbackProfileKey: string;
    appliedOrgCustomizationKey: string;
    resolvedMode: "seed_only" | "persisted_preferred";
    ignoredOverridesCount: number;
    runtimeDiagnosticsCount: number;
  };
  recentChanges: {
    availability: "available" | "empty" | "not_available";
    note: string;
    items: Array<{
      id: string;
      targetType: "org_template_override" | "org_settings" | "org_ai_settings" | "org_feature_flags";
      targetKey: string | null;
      actionType: string;
      versionLabel: string;
      versionNumber: number;
      createdAt: string;
      actorUserId: string;
      runtimeImpactSummary: string | null;
      diagnosticsPreview: string[];
      hasIgnoredOrForbiddenDiagnostics: boolean;
      hasConflictDiagnostics: boolean;
      rollbackAvailability: "supported" | "not_supported";
    }>;
  };
  healthSummary: {
    recentChangeCount: number;
    recentIgnoredOrForbiddenCount: number;
    recentConflictCount: number;
    recentRollbackCount: number;
    fallbackOrUnavailableCount: number;
    note: string;
  };
  domainCards: Array<{
    domainKey: "template_override" | "org_config" | "runtime_debug";
    title: string;
    href: string;
    status: "available" | "degraded" | "not_available";
    summary: string;
    latestChangedAt: string | null;
    rollbackSupportSummary: string;
    note: string;
  }>;
  statusSignals: Array<{
    status: "fallback" | "not_available" | "degraded";
    domain: "runtime" | "template_override" | "org_settings" | "org_ai_settings" | "org_feature_flags" | "audit_history";
    detail: string;
  }>;
  limitations: string[];
}

export interface ConfigTimelineViewerPayload {
  generatedAt: string;
  timeline: {
    availability: "available" | "empty" | "not_available";
    note: string;
    items: Array<{
      id: string;
      targetType: "org_template_override" | "org_settings" | "org_ai_settings" | "org_feature_flags";
      targetKey: string | null;
      actionType: string;
      versionLabel: string;
      versionNumber: number;
      createdAt: string;
      actorUserId: string;
      availability: "available" | "summary_only" | "not_available";
      diagnosticsPreview: string[];
      runtimeImpactSummary: string | null;
      rollbackSource: {
        sourceAuditId: string | null;
        sourceVersionLabel: string | null;
        sourceVersionNumber: number | null;
        previewGeneratedAt: string | null;
      } | null;
      detail: {
        beforeSummary: Record<string, unknown> | null;
        afterSummary: Record<string, unknown> | null;
        snapshotSummary: Record<string, unknown> | null;
        diagnosticsSummary: Record<string, unknown> | null;
        diffSummary: {
          status: "available" | "summary_only" | "not_available";
          compareSource: "payload_preview" | "normalized_payload" | "summary_object";
          changedKeys: string[];
          addedKeys: string[];
          removedKeys: string[];
          totalChanged: number;
          redactedFields: string[];
          note: string;
        };
        note: string;
      };
    }>;
  };
  statusSignals: Array<{
    status: "fallback" | "degraded" | "not_available";
    domain: "runtime" | "timeline" | "template_override" | "org_config";
    detail: string;
  }>;
  limitations: string[];
}

async function readPayload<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as ApiPayload<T>;
  if (!response.ok || !payload.success || !payload.data) {
    throw new Error(payload.error ?? "Request failed");
  }
  return payload.data;
}

async function readPayloadWithStatus<T>(response: Response): Promise<{
  ok: boolean;
  status: number;
  error: string | null;
  data: T | null;
}> {
  const payload = (await response.json().catch(() => null)) as ApiPayloadWithStatus<T> | null;
  if (!payload) {
    return {
      ok: false,
      status: response.status,
      error: "Request failed",
      data: null
    };
  }

  return {
    ok: response.ok && payload.success,
    status: response.status,
    error: payload.error,
    data: payload.data
  };
}

function asObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

export const settingsClientService = {
  async getOrgSettings(): Promise<{ settings: OrgSettings }> {
    const response = await fetch("/api/settings/org", { method: "GET" });
    return readPayload<{ settings: OrgSettings }>(response);
  },

  async updateOrgSettings(patch: Partial<{
    orgDisplayName: string;
    brandName: string;
    industryHint: string | null;
    timezone: string;
    locale: string;
    defaultCustomerStages: string[];
    defaultOpportunityStages: string[];
    defaultAlertRules: Record<string, number>;
    defaultFollowupSlaDays: number;
    onboardingCompleted: boolean;
  }>): Promise<{ settings: OrgSettings }> {
    const response = await fetch("/api/settings/org", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch)
    });
    return readPayload<{ settings: OrgSettings }>(response);
  },

  async getTeamSettings(): Promise<{
    role: string;
    canManageTeam: boolean;
    canViewUsage: boolean;
    members: OrgMembership[];
    invites: OrgInvite[];
  }> {
    const response = await fetch("/api/settings/team", { method: "GET" });
    return readPayload(response);
  },

  async inviteMember(payload: { email: string; intendedRole: OrgMembership["role"]; expiresAt?: string }): Promise<{ invite: OrgInvite; inviteLink: string }> {
    const response = await fetch("/api/settings/team/invite", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload(response);
  },

  async updateMemberRole(payload: { membershipId: string; role: OrgMembership["role"] }): Promise<{ membership: OrgMembership }> {
    const response = await fetch("/api/settings/team/update-role", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload(response);
  },

  async updateMemberSeatStatus(payload: { membershipId: string; seatStatus: OrgMembership["seatStatus"] }): Promise<{ membership: OrgMembership }> {
    const response = await fetch("/api/settings/team/update-seat-status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload(response);
  },

  async getAiSettings(): Promise<{
    role: string;
    canManage: boolean;
    aiStatus: {
      settings: OrgAiSettings;
      providerConfigured: boolean;
      providerReason: string | null;
    };
    featureFlags: OrgFeatureFlag[];
  }> {
    const response = await fetch("/api/settings/ai", { method: "GET" });
    return readPayload(response);
  },

  async updateAiSettings(patch: Partial<{
    provider: OrgAiSettings["provider"];
    modelDefault: string;
    modelReasoning: string;
    fallbackMode: OrgAiSettings["fallbackMode"];
    autoAnalysisEnabled: boolean;
    autoPlanEnabled: boolean;
    autoBriefEnabled: boolean;
    autoTouchpointReviewEnabled: boolean;
    humanReviewRequiredForSensitiveActions: boolean;
    maxDailyAiRuns: number | null;
    maxMonthlyAiRuns: number | null;
    featureFlags: Partial<Record<OrgFeatureKey, boolean>>;
  }>): Promise<{
    settings: OrgAiSettings;
    status: {
      settings: OrgAiSettings;
      providerConfigured: boolean;
      providerReason: string | null;
    };
    featureFlags: OrgFeatureFlag[];
  }> {
    const response = await fetch("/api/settings/ai", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch)
    });
    return readPayload(response);
  },

  async getUsageSettings(): Promise<{
    usage: {
      daily: OrgUsageCounter | null;
      monthly: OrgUsageCounter | null;
      topUsersMonthly: UserUsageCounter[];
    };
    entitlement: EntitlementStatus;
    featureFlags: Record<string, boolean>;
    summary: {
      summary: {
        usageSummary: string;
        hotFeatures: string[];
        underusedFeatures: string[];
        quotaRisks: string[];
        recommendedAdjustments: string[];
      };
      usedFallback: boolean;
      fallbackReason: string | null;
    };
  }> {
    const response = await fetch("/api/settings/usage", { method: "GET" });
    return readPayload(response);
  },

  async getRuntimeDebugPanel(): Promise<{
    role: string;
    panel: RuntimeExplainDebugPanelPayload;
  }> {
    const response = await fetch("/api/settings/runtime-debug", { method: "GET" });
    return readPayload(response);
  },

  async getConfigOperationsHub(): Promise<{
    role: string;
    hub: ConfigOperationsHubPayload;
  }> {
    const response = await fetch("/api/settings/config-ops", { method: "GET" });
    return readPayload(response);
  },

  async getConfigTimelineViewer(): Promise<{
    role: string;
    timeline: ConfigTimelineViewerPayload;
  }> {
    const response = await fetch("/api/settings/config-timeline", { method: "GET" });
    return readPayload(response);
  },

  async getOrgConfigState(): Promise<OrgConfigEditorStatePayload> {
    const response = await fetch("/api/settings/org-config/state", { method: "GET" });
    return readPayload(response);
  },

  async previewOrgConfigWrite(payload: {
    targetType: OrgConfigEditorTargetType;
    patch: Record<string, unknown>;
  }): Promise<OrgConfigWritePreviewPayload> {
    const response = await fetch("/api/settings/org-config/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload(response);
  },

  async executeOrgConfigWrite(payload: {
    targetType: OrgConfigEditorTargetType;
    patch: Record<string, unknown>;
    expectedVersion: OrgConfigExpectedVersion;
  }): Promise<
    | {
        status: "success";
        data: OrgConfigWriteSuccessPayload;
      }
    | {
        status: "conflict";
        conflict: OrgConfigConflictPayload;
      }
  > {
    if (payload.targetType === "org_settings") {
      const response = await fetch("/api/settings/org", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...payload.patch,
          expectedVersion: payload.expectedVersion
        })
      });
      const parsed = await readPayloadWithStatus<Record<string, unknown> | OrgConfigConflictPayload>(response);
      if (parsed.ok && parsed.data) {
        const data = asObject(parsed.data);
        return {
          status: "success",
          data: {
            targetType: "org_settings",
            writeDiagnostics: asObject(data.writeDiagnostics),
            auditDraft: asObject(data.auditDraft),
            persistedAudit: {
              status: String(asObject(data.persistedAudit).status ?? "not_available") as "persisted" | "not_available",
              record: (() => {
                const record = asObject(asObject(data.persistedAudit).record);
                return Object.keys(record).length > 0 ? record : null;
              })(),
              reason:
                typeof asObject(data.persistedAudit).reason === "string"
                  ? (asObject(data.persistedAudit).reason as string)
                  : null
            },
            concurrency: {
              expectedVersion: asObject(asObject(data.concurrency).expectedVersion),
              beforeWrite: asObject(asObject(data.concurrency).beforeWrite),
              afterWrite: asObject(asObject(data.concurrency).afterWrite)
            }
          }
        };
      }
      if (parsed.status === 409 && parsed.data) {
        return {
          status: "conflict",
          conflict: parsed.data as OrgConfigConflictPayload
        };
      }
      throw new Error(parsed.error ?? "execute_org_settings_write_failed");
    }

    const aiBody =
      payload.targetType === "org_ai_settings"
        ? {
            ...payload.patch,
            expectedVersion: {
              orgAiSettings: payload.expectedVersion
            }
          }
        : {
            featureFlags: payload.patch,
            expectedVersion: {
              orgFeatureFlags: payload.expectedVersion
            }
          };

    const response = await fetch("/api/settings/ai", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(aiBody)
    });
    const parsed = await readPayloadWithStatus<Record<string, unknown> | OrgConfigConflictPayload>(response);
    if (parsed.ok && parsed.data) {
      const data = asObject(parsed.data);
      const governance = asObject(data.governance);
      const writeData =
        payload.targetType === "org_ai_settings"
          ? asObject(governance.orgAiSettings)
          : asObject(governance.orgFeatureFlags);
      if (Object.keys(writeData).length === 0) {
        throw new Error("org_config_governance_summary_missing");
      }

      return {
        status: "success",
        data: {
          targetType: payload.targetType,
          writeDiagnostics: asObject(writeData.writeDiagnostics),
          auditDraft: asObject(writeData.auditDraft),
          persistedAudit: {
            status: String(asObject(writeData.persistedAudit).status ?? "not_available") as "persisted" | "not_available",
            record: (() => {
              const record = asObject(asObject(writeData.persistedAudit).record);
              return Object.keys(record).length > 0 ? record : null;
            })(),
            reason:
              typeof asObject(writeData.persistedAudit).reason === "string"
                ? (asObject(writeData.persistedAudit).reason as string)
                : null
          },
          concurrency: {
            expectedVersion: asObject(asObject(writeData.concurrency).expectedVersion),
            beforeWrite: asObject(asObject(writeData.concurrency).beforeWrite),
            afterWrite: asObject(asObject(writeData.concurrency).afterWrite)
          }
        }
      };
    }
    if (parsed.status === 409 && parsed.data) {
      return {
        status: "conflict",
        conflict: parsed.data as OrgConfigConflictPayload
      };
    }
    throw new Error(parsed.error ?? "execute_org_config_write_failed");
  },

  async previewOrgConfigRollback(payload: {
    targetType: OrgConfigEditorTargetType;
    targetAuditId?: string;
    targetVersionLabel?: string;
    targetVersionNumber?: number;
  }): Promise<OrgConfigRollbackPreviewPayload> {
    const response = await fetch("/api/settings/org-config/rollback-preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload(response);
  },

  async executeOrgConfigRollback(payload: {
    targetType: OrgConfigEditorTargetType;
    targetAuditId?: string;
    targetVersionLabel?: string;
    targetVersionNumber?: number;
    expectedVersion: OrgConfigExpectedVersion;
  }): Promise<
    | {
        status: "success";
        data: OrgConfigRollbackExecuteSuccessPayload;
      }
    | {
        status: "conflict";
        conflict: OrgConfigConflictPayload;
      }
  > {
    const response = await fetch("/api/settings/org-config/rollback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const parsed = await readPayloadWithStatus<OrgConfigRollbackExecuteSuccessPayload | Record<string, unknown>>(response);
    if (parsed.ok && parsed.data) {
      return {
        status: "success",
        data: parsed.data as OrgConfigRollbackExecuteSuccessPayload
      };
    }
    if (parsed.status === 409 && parsed.data) {
      const data = asObject(parsed.data);
      return {
        status: "conflict",
        conflict: {
          conflict: true,
          conflictReason: typeof data.conflictReason === "string" ? data.conflictReason : null,
          currentVersion: asObject(data.currentVersion),
          expectedVersion: asObject(data.expectedVersion),
          diagnostics: asStringArray(data.diagnostics)
        }
      };
    }
    throw new Error(parsed.error ?? "execute_org_config_rollback_failed");
  },

  async getOnboardingSettings(): Promise<{
    role: string;
    checklist: OnboardingChecklist;
    settings: OrgSettings;
    aiStatus: {
      settings: OrgAiSettings;
      providerConfigured: boolean;
      providerReason: string | null;
    };
    featureFlags: Record<string, boolean>;
    entitlement: EntitlementStatus;
    latestRuns: OnboardingRun[];
    currentTemplate: {
      templateKey: string;
      displayName: string;
    } | null;
    recommendation: OnboardingRecommendationResult;
    recommendationUsedFallback: boolean;
    recommendationFallbackReason: string | null;
  }> {
    const response = await fetch("/api/settings/onboarding", { method: "GET" });
    return readPayload(response);
  },

  async runOnboarding(payload: { runType: "first_time_setup" | "trial_bootstrap" | "demo_seed" | "reinitialize_demo" }): Promise<{
    run: OnboardingRun;
    message: string;
    partialSuccess: boolean;
    detail: Record<string, unknown>;
  }> {
    const response = await fetch("/api/settings/onboarding/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload(response);
  },

  async runDemoSeed(): Promise<{
    runId: string;
    status: "completed" | "failed";
    partialSuccess: boolean;
    summary: string;
    steps: Array<{
      name: string;
      success: boolean;
      inserted: number;
      message: string;
    }>;
    inserted: Record<string, number>;
  }> {
    const response = await fetch("/api/settings/demo-seed/run", {
      method: "POST"
    });
    return readPayload(response);
  },

  async getTemplateCenter(): Promise<{
    role: string;
    templates: IndustryTemplate[];
    currentTemplate: IndustryTemplate | null;
    currentAssignment: IndustryTemplateContext["assignment"];
    recentRuns: TemplateApplicationRun[];
  }> {
    const response = await fetch("/api/settings/templates", { method: "GET" });
    return readPayload(response);
  },

  async getTemplateDetail(templateIdOrKey: string): Promise<{
    template: IndustryTemplate;
    scenarioPacks: IndustryTemplateContext["scenarioPacks"];
    seededPlaybookTemplates: IndustryTemplateContext["seededPlaybookTemplates"];
  }> {
    const response = await fetch(`/api/settings/templates/${encodeURIComponent(templateIdOrKey)}`, { method: "GET" });
    return readPayload(response);
  },

  async getCurrentTemplate(): Promise<IndustryTemplateContext & { role: string }> {
    const response = await fetch("/api/settings/templates/current", { method: "GET" });
    return readPayload(response);
  },

  async recommendTemplate(industryHint?: string | null): Promise<{
    recommendation: TemplateFitRecommendation;
    usedFallback: boolean;
    fallbackReason: string | null;
  }> {
    const response = await fetch("/api/settings/templates/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ industryHint: industryHint ?? null })
    });
    return readPayload(response);
  },

  async previewTemplateApply(payload: {
    templateIdOrKey: string;
    applyMode?: TemplateApplyMode;
    applyStrategy?: TemplateApplyStrategy;
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
    template: IndustryTemplate;
  }> {
    const response = await fetch("/api/settings/templates/preview-apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload(response);
  },

  async applyTemplate(payload: {
    templateIdOrKey: string;
    applyMode?: TemplateApplyMode;
    applyStrategy?: TemplateApplyStrategy;
    generateDemoSeed?: boolean;
    overrides?: Array<{
      overrideType: OrgTemplateOverride["overrideType"];
      overridePayload: Record<string, unknown>;
    }>;
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
    const response = await fetch("/api/settings/templates/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload(response);
  },

  async upsertTemplateOverride(payload: {
    templateId?: string;
    overrideType: OrgTemplateOverride["overrideType"];
    overridePayload: Record<string, unknown>;
    expectedVersion?: TemplateOverrideExpectedVersion;
  }): Promise<{
    override: OrgTemplateOverride;
    writeDiagnostics: Record<string, unknown>;
    diagnosticsSummary: Record<string, unknown>;
    auditDraft: Record<string, unknown>;
    concurrency: {
      expectedVersion: Record<string, unknown> | null;
      beforeWrite: Record<string, unknown>;
      afterWrite: Record<string, unknown>;
    };
    persistedAudit: {
      status: "persisted" | "not_available";
      record: Record<string, unknown> | null;
      reason: string | null;
    };
  }> {
    const response = await fetch("/api/settings/templates/overrides", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload(response);
  },

  async getCurrentTemplateOverrideState(payload: {
    templateId?: string;
    overrideType: OrgTemplateOverride["overrideType"];
  }): Promise<TemplateOverrideEditorStatePayload> {
    const response = await fetch("/api/settings/templates/overrides/state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload(response);
  },

  async previewTemplateOverrideWrite(payload: {
    templateId?: string;
    overrideType: OrgTemplateOverride["overrideType"];
    overridePayload: Record<string, unknown>;
  }): Promise<TemplateOverrideWritePreviewPayload> {
    const response = await fetch("/api/settings/templates/overrides/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload(response);
  },

  async executeTemplateOverrideWrite(payload: {
    templateId?: string;
    overrideType: OrgTemplateOverride["overrideType"];
    overridePayload: Record<string, unknown>;
    expectedVersion: TemplateOverrideExpectedVersion;
  }): Promise<
    | {
        status: "success";
        data: TemplateOverrideWriteSuccessPayload;
      }
    | {
        status: "conflict";
        conflict: TemplateOverrideConflictPayload;
      }
  > {
    const response = await fetch("/api/settings/templates/overrides", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const parsed = await readPayloadWithStatus<TemplateOverrideWriteSuccessPayload | TemplateOverrideConflictPayload>(response);
    if (parsed.ok && parsed.data) {
      return {
        status: "success",
        data: parsed.data as TemplateOverrideWriteSuccessPayload
      };
    }
    if (parsed.status === 409 && parsed.data) {
      return {
        status: "conflict",
        conflict: parsed.data as TemplateOverrideConflictPayload
      };
    }
    throw new Error(parsed.error ?? "execute_template_override_write_failed");
  },

  async previewTemplateOverrideRollback(payload: {
    templateId?: string;
    overrideType: OrgTemplateOverride["overrideType"];
    targetAuditId?: string;
    targetVersionLabel?: string;
    targetVersionNumber?: number;
  }): Promise<TemplateOverrideRollbackPreviewPayload> {
    const response = await fetch("/api/settings/templates/overrides/rollback-preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload(response);
  },

  async executeTemplateOverrideRollback(payload: {
    templateId?: string;
    overrideType: OrgTemplateOverride["overrideType"];
    targetAuditId?: string;
    targetVersionLabel?: string;
    targetVersionNumber?: number;
    expectedVersion: TemplateOverrideExpectedVersion;
  }): Promise<
    | {
        status: "success";
        data: TemplateOverrideRollbackExecuteSuccessPayload;
      }
    | {
        status: "conflict";
        conflict: TemplateOverrideConflictPayload;
      }
  > {
    const response = await fetch("/api/settings/templates/overrides/rollback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const parsed = await readPayloadWithStatus<TemplateOverrideRollbackExecuteSuccessPayload | TemplateOverrideConflictPayload>(response);
    if (parsed.ok && parsed.data) {
      return {
        status: "success",
        data: parsed.data as TemplateOverrideRollbackExecuteSuccessPayload
      };
    }
    if (parsed.status === 409 && parsed.data) {
      return {
        status: "conflict",
        conflict: parsed.data as TemplateOverrideConflictPayload
      };
    }
    throw new Error(parsed.error ?? "execute_template_override_rollback_failed");
  }
};
