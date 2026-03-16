import { BUILTIN_INDUSTRY_TEMPLATE_SEEDS } from "@/data/industry-templates";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import type {
  IndustryTemplate,
  IndustryTemplateContext,
  IndustryTemplateSeed,
  OrgTemplateAssignment,
  OrgTemplateOverride,
  ScenarioPack,
  SeededPlaybookTemplate
} from "@/types/productization";

type DbClient = ServerSupabaseClient;

interface IndustryTemplateRow {
  id: string;
  template_key: string;
  display_name: string;
  industry_family: string;
  status: string;
  summary: string;
  template_payload: Record<string, unknown> | null;
  is_system_template: boolean;
  created_at: string;
  updated_at: string;
}

interface ScenarioPackRow {
  id: string;
  template_id: string;
  pack_type: string;
  title: string;
  summary: string;
  pack_payload: Record<string, unknown> | null;
  status: string;
  created_at: string;
  updated_at: string;
}

interface SeededPlaybookTemplateRow {
  id: string;
  template_id: string;
  playbook_type: string;
  title: string;
  summary: string;
  payload: Record<string, unknown> | null;
  status: string;
  created_at: string;
  updated_at: string;
}

interface OrgTemplateAssignmentRow {
  id: string;
  org_id: string;
  template_id: string;
  assignment_status: string;
  apply_mode: string;
  apply_strategy: string;
  applied_by: string;
  applied_at: string;
  created_at: string;
  updated_at: string;
}

interface OrgTemplateOverrideRow {
  id: string;
  org_id: string;
  template_id: string;
  override_type: string;
  override_payload: Record<string, unknown> | null;
  created_by: string;
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

function mapTemplateRow(row: IndustryTemplateRow): IndustryTemplate {
  return {
    id: row.id,
    templateKey: row.template_key,
    displayName: row.display_name,
    industryFamily: row.industry_family as IndustryTemplate["industryFamily"],
    status: row.status as IndustryTemplate["status"],
    summary: row.summary,
    templatePayload: asObject(row.template_payload),
    isSystemTemplate: row.is_system_template,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

function mapScenarioPackRow(row: ScenarioPackRow): ScenarioPack {
  return {
    id: row.id,
    templateId: row.template_id,
    packType: row.pack_type as ScenarioPack["packType"],
    title: row.title,
    summary: row.summary,
    packPayload: asObject(row.pack_payload),
    status: row.status as ScenarioPack["status"],
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

function mapSeededPlaybookTemplateRow(row: SeededPlaybookTemplateRow): SeededPlaybookTemplate {
  return {
    id: row.id,
    templateId: row.template_id,
    playbookType: row.playbook_type as SeededPlaybookTemplate["playbookType"],
    title: row.title,
    summary: row.summary,
    payload: asObject(row.payload),
    status: row.status as SeededPlaybookTemplate["status"],
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

function mapAssignmentRow(row: OrgTemplateAssignmentRow): OrgTemplateAssignment {
  return {
    id: row.id,
    orgId: row.org_id,
    templateId: row.template_id,
    assignmentStatus: row.assignment_status as OrgTemplateAssignment["assignmentStatus"],
    applyMode: row.apply_mode as OrgTemplateAssignment["applyMode"],
    applyStrategy: row.apply_strategy as OrgTemplateAssignment["applyStrategy"],
    appliedBy: row.applied_by,
    appliedAt: row.applied_at,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

function mapOverrideRow(row: OrgTemplateOverrideRow): OrgTemplateOverride {
  return {
    id: row.id,
    orgId: row.org_id,
    templateId: row.template_id,
    overrideType: row.override_type as OrgTemplateOverride["overrideType"],
    overridePayload: asObject(row.override_payload),
    createdBy: row.created_by,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

function seedToTemplate(seed: IndustryTemplateSeed): IndustryTemplate {
  const now = nowIso();
  return {
    id: `builtin:${seed.templateKey}`,
    templateKey: seed.templateKey,
    displayName: seed.displayName,
    industryFamily: seed.industryFamily,
    status: "active",
    summary: seed.summary,
    templatePayload: seed.templatePayload,
    isSystemTemplate: true,
    createdAt: now,
    updatedAt: now
  };
}

function seedToScenarioPacks(seed: IndustryTemplateSeed, templateId: string): ScenarioPack[] {
  const now = nowIso();
  return seed.scenarioPacks.map((pack, idx) => ({
    id: `${templateId}:pack:${idx + 1}`,
    templateId,
    packType: pack.packType,
    title: pack.title,
    summary: pack.summary,
    packPayload: pack.packPayload,
    status: "active",
    createdAt: now,
    updatedAt: now
  }));
}

function seedToPlaybookTemplates(seed: IndustryTemplateSeed, templateId: string): SeededPlaybookTemplate[] {
  const now = nowIso();
  return seed.seededPlaybookTemplates.map((item, idx) => ({
    id: `${templateId}:playbook:${idx + 1}`,
    templateId,
    playbookType: item.playbookType,
    title: item.title,
    summary: item.summary,
    payload: item.payload,
    status: "active",
    createdAt: now,
    updatedAt: now
  }));
}

function findSeedByKey(templateKey: string): IndustryTemplateSeed | null {
  return BUILTIN_INDUSTRY_TEMPLATE_SEEDS.find((item) => item.templateKey === templateKey) ?? null;
}

export async function listIndustryTemplates(params: {
  supabase: DbClient;
  includeArchived?: boolean;
}): Promise<IndustryTemplate[]> {
  let query = (params.supabase as any)
    .from("industry_templates")
    .select("*")
    .order("is_system_template", { ascending: false })
    .order("display_name", { ascending: true });

  if (!params.includeArchived) {
    query = query.neq("status", "archived");
  }

  const res = await query;
  if (res.error) {
    if (res.error.message.includes("industry_templates") || res.error.message.includes("does not exist")) {
      return BUILTIN_INDUSTRY_TEMPLATE_SEEDS.map(seedToTemplate);
    }
    throw new Error(res.error.message);
  }

  const rows = (res.data ?? []) as IndustryTemplateRow[];
  if (rows.length > 0) {
    return rows.map(mapTemplateRow);
  }

  return BUILTIN_INDUSTRY_TEMPLATE_SEEDS.map(seedToTemplate);
}

export async function listScenarioPacksByTemplateIds(params: {
  supabase: DbClient;
  templateIds: string[];
}): Promise<ScenarioPack[]> {
  if (params.templateIds.length === 0) return [];
  const res = await (params.supabase as any)
    .from("scenario_packs")
    .select("*")
    .in("template_id", params.templateIds)
    .neq("status", "archived")
    .order("created_at", { ascending: true });
  if (res.error) {
    if (res.error.message.includes("scenario_packs") || res.error.message.includes("does not exist")) {
      return [];
    }
    throw new Error(res.error.message);
  }
  return ((res.data ?? []) as ScenarioPackRow[]).map(mapScenarioPackRow);
}

export async function listSeededPlaybookTemplatesByTemplateIds(params: {
  supabase: DbClient;
  templateIds: string[];
}): Promise<SeededPlaybookTemplate[]> {
  if (params.templateIds.length === 0) return [];
  const res = await (params.supabase as any)
    .from("seeded_playbook_templates")
    .select("*")
    .in("template_id", params.templateIds)
    .neq("status", "archived")
    .order("created_at", { ascending: true });
  if (res.error) {
    if (res.error.message.includes("seeded_playbook_templates") || res.error.message.includes("does not exist")) {
      return [];
    }
    throw new Error(res.error.message);
  }
  return ((res.data ?? []) as SeededPlaybookTemplateRow[]).map(mapSeededPlaybookTemplateRow);
}

export async function getIndustryTemplateDetail(params: {
  supabase: DbClient;
  templateIdOrKey: string;
}): Promise<{
  template: IndustryTemplate;
  scenarioPacks: ScenarioPack[];
  seededPlaybookTemplates: SeededPlaybookTemplate[];
}> {
  const list = await listIndustryTemplates({ supabase: params.supabase, includeArchived: true });
  const template =
    list.find((item) => item.id === params.templateIdOrKey || item.templateKey === params.templateIdOrKey) ?? null;
  if (!template) {
    throw new Error("industry_template_not_found");
  }

  if (template.id.startsWith("builtin:")) {
    const seed = findSeedByKey(template.templateKey);
    if (!seed) throw new Error("industry_template_seed_not_found");
    return {
      template,
      scenarioPacks: seedToScenarioPacks(seed, template.id),
      seededPlaybookTemplates: seedToPlaybookTemplates(seed, template.id)
    };
  }

  const [scenarioPacks, seededPlaybookTemplates] = await Promise.all([
    listScenarioPacksByTemplateIds({
      supabase: params.supabase,
      templateIds: [template.id]
    }),
    listSeededPlaybookTemplatesByTemplateIds({
      supabase: params.supabase,
      templateIds: [template.id]
    })
  ]);

  const fallbackSeed = findSeedByKey(template.templateKey);
  return {
    template,
    scenarioPacks: scenarioPacks.length > 0 || !fallbackSeed ? scenarioPacks : seedToScenarioPacks(fallbackSeed, template.id),
    seededPlaybookTemplates:
      seededPlaybookTemplates.length > 0 || !fallbackSeed
        ? seededPlaybookTemplates
        : seedToPlaybookTemplates(fallbackSeed, template.id)
  };
}

export async function getCurrentOrgTemplateContext(params: {
  supabase: DbClient;
  orgId: string;
}): Promise<IndustryTemplateContext> {
  const assignmentRes = await (params.supabase as any)
    .from("org_template_assignments")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("assignment_status", "active")
    .order("applied_at", { ascending: false })
    .limit(1)
    .maybeSingle();
  if (assignmentRes.error) {
    if (assignmentRes.error.message.includes("org_template_assignments") || assignmentRes.error.message.includes("does not exist")) {
      return {
        assignment: null,
        template: null,
        scenarioPacks: [],
        seededPlaybookTemplates: [],
        overrides: []
      };
    }
    throw new Error(assignmentRes.error.message);
  }

  const assignment = assignmentRes.data ? mapAssignmentRow(assignmentRes.data as OrgTemplateAssignmentRow) : null;
  if (!assignment) {
    return {
      assignment: null,
      template: null,
      scenarioPacks: [],
      seededPlaybookTemplates: [],
      overrides: []
    };
  }

  const [detail, overrideRes] = await Promise.all([
    getIndustryTemplateDetail({
      supabase: params.supabase,
      templateIdOrKey: assignment.templateId
    }),
    (params.supabase as any)
      .from("org_template_overrides")
      .select("*")
      .eq("org_id", params.orgId)
      .eq("template_id", assignment.templateId)
      .order("created_at", { ascending: false })
  ]);

  if (overrideRes.error) {
    if (overrideRes.error.message.includes("org_template_overrides") || overrideRes.error.message.includes("does not exist")) {
      return {
        assignment,
        template: detail.template,
        scenarioPacks: detail.scenarioPacks,
        seededPlaybookTemplates: detail.seededPlaybookTemplates,
        overrides: []
      };
    }
    throw new Error(overrideRes.error.message);
  }

  return {
    assignment,
    template: detail.template,
    scenarioPacks: detail.scenarioPacks,
    seededPlaybookTemplates: detail.seededPlaybookTemplates,
    overrides: ((overrideRes.data ?? []) as OrgTemplateOverrideRow[]).map(mapOverrideRow)
  };
}

export async function listOrgTemplateAssignments(params: {
  supabase: DbClient;
  orgId: string;
  limit?: number;
}): Promise<OrgTemplateAssignment[]> {
  const res = await (params.supabase as any)
    .from("org_template_assignments")
    .select("*")
    .eq("org_id", params.orgId)
    .order("applied_at", { ascending: false })
    .limit(params.limit ?? 20);
  if (res.error) {
    if (res.error.message.includes("org_template_assignments") || res.error.message.includes("does not exist")) {
      return [];
    }
    throw new Error(res.error.message);
  }
  return ((res.data ?? []) as OrgTemplateAssignmentRow[]).map(mapAssignmentRow);
}

export async function upsertOrgTemplateOverride(params: {
  supabase: DbClient;
  orgId: string;
  templateId: string;
  overrideType: OrgTemplateOverride["overrideType"];
  overridePayload: Record<string, unknown>;
  actorUserId: string;
}): Promise<OrgTemplateOverride> {
  const res = await (params.supabase as any)
    .from("org_template_overrides")
    .upsert(
      {
        org_id: params.orgId,
        template_id: params.templateId,
        override_type: params.overrideType,
        override_payload: params.overridePayload,
        created_by: params.actorUserId
      },
      {
        onConflict: "org_id,template_id,override_type"
      }
    )
    .select("*")
    .single();

  if (res.error) throw new Error(res.error.message);
  return mapOverrideRow(res.data as OrgTemplateOverrideRow);
}
