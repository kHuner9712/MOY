import {
  canManageTemplates,
  canViewManagerWorkspace,
  resolveEffectiveOrgRole,
  type RoleCapabilityInput
} from "@/lib/role-capability";
import type { OrgMemberRole, OrgTemplateOverrideType } from "@/types/productization";
import type {
  TemplateOverrideConflictPayload,
  TemplateOverrideExpectedVersion,
  TemplateOverrideRollbackPreviewPayload
} from "@/services/settings-client-service";

export interface TemplateOverrideEditorAccessState {
  canAccess: boolean;
  canPreview: boolean;
  canWrite: boolean;
  canExecuteRollback: boolean;
  effectiveOrgRole: OrgMemberRole | null;
}

function asObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

export function resolveTemplateOverrideEditorAccess(params: {
  user: RoleCapabilityInput | null | undefined;
  templateCenterRole?: string | null;
}): TemplateOverrideEditorAccessState {
  const roleFromCenter = params.templateCenterRole as OrgMemberRole | null | undefined;
  const capabilityInput: RoleCapabilityInput = {
    role: params.user?.role ?? null,
    orgRole: roleFromCenter ?? params.user?.orgRole ?? null
  };
  return {
    canAccess: canViewManagerWorkspace(capabilityInput),
    canPreview: canViewManagerWorkspace(capabilityInput),
    canWrite: canManageTemplates(capabilityInput),
    canExecuteRollback: canManageTemplates(capabilityInput),
    effectiveOrgRole: resolveEffectiveOrgRole(capabilityInput)
  };
}

export function extractTemplateOverrideConflictPayload(
  value: unknown
): TemplateOverrideConflictPayload | null {
  const obj = asObject(value);
  const conflict = obj.conflict === true;
  if (!conflict) return null;
  return {
    conflict: true,
    conflictReason: asString(obj.conflictReason),
    currentVersion: asObject(obj.currentVersion),
    expectedVersion: asObject(obj.expectedVersion),
    diagnostics: asStringArray(obj.diagnostics)
  };
}

export function buildRollbackExecutePayloadFromPreview(params: {
  overrideType: OrgTemplateOverrideType;
  templateId?: string;
  preview: TemplateOverrideRollbackPreviewPayload["preview"] | null;
}): {
  templateId?: string;
  overrideType: OrgTemplateOverrideType;
  targetAuditId?: string;
  targetVersionLabel?: string;
  targetVersionNumber?: number;
  expectedVersion: TemplateOverrideExpectedVersion;
} | null {
  if (!params.preview) return null;
  const expectedVersion = params.preview.concurrency.expectedVersion;
  if (!expectedVersion) return null;

  const targetVersionLabel = asString(params.preview.targetVersion.versionLabel);
  const targetAuditId = asString(params.preview.targetVersion.auditId);
  const targetVersionNumber =
    typeof params.preview.targetVersion.versionNumber === "number" && params.preview.targetVersion.versionNumber > 0
      ? params.preview.targetVersion.versionNumber
      : undefined;

  if (!targetAuditId && !targetVersionLabel && typeof targetVersionNumber !== "number") {
    return null;
  }

  return {
    templateId: params.templateId,
    overrideType: params.overrideType,
    targetAuditId: targetAuditId ?? undefined,
    targetVersionLabel: targetVersionLabel ?? undefined,
    targetVersionNumber,
    expectedVersion
  };
}
