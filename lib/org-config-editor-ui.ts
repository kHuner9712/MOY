import {
  canManageOrgCustomization,
  canViewManagerWorkspace,
  resolveEffectiveOrgRole,
  type RoleCapabilityInput
} from "@/lib/role-capability";
import type { OrgMemberRole } from "@/types/productization";

export interface OrgConfigEditorAccessState {
  canAccess: boolean;
  canPreview: boolean;
  canWrite: boolean;
  effectiveOrgRole: OrgMemberRole | null;
}

export interface OrgConfigConflictPayload {
  conflict: true;
  conflictReason: string | null;
  currentVersion: Record<string, unknown> | null;
  expectedVersion: Record<string, unknown> | null;
  diagnostics: string[];
}

function asObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

export function resolveOrgConfigEditorAccess(params: {
  user: RoleCapabilityInput | null | undefined;
  stateRole?: string | null;
}): OrgConfigEditorAccessState {
  const capabilityInput: RoleCapabilityInput = {
    role: params.user?.role ?? null,
    orgRole: (params.stateRole as OrgMemberRole | null | undefined) ?? params.user?.orgRole ?? null
  };
  return {
    canAccess: canViewManagerWorkspace(capabilityInput),
    canPreview: canManageOrgCustomization(capabilityInput),
    canWrite: canManageOrgCustomization(capabilityInput),
    effectiveOrgRole: resolveEffectiveOrgRole(capabilityInput)
  };
}

export function extractOrgConfigConflictPayload(value: unknown): OrgConfigConflictPayload | null {
  const obj = asObject(value);
  if (obj.conflict !== true) return null;
  return {
    conflict: true,
    conflictReason: asString(obj.conflictReason),
    currentVersion: asObject(obj.currentVersion),
    expectedVersion: asObject(obj.expectedVersion),
    diagnostics: asStringArray(obj.diagnostics)
  };
}
