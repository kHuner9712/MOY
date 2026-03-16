import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { createPlaybookWithEntries } from "@/services/playbook-service";
import type { IndustryTemplate, SeededPlaybookTemplate, TemplateApplyMode } from "@/types/productization";

type DbClient = ServerSupabaseClient;

function asRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

export async function seedPlaybooksFromTemplate(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  template: IndustryTemplate;
  seededTemplates: SeededPlaybookTemplate[];
  applyMode: TemplateApplyMode;
}): Promise<{
  createdCount: number;
  skippedCount: number;
  createdPlaybookIds: string[];
}> {
  let createdCount = 0;
  let skippedCount = 0;
  const createdPlaybookIds: string[] = [];

  for (const seed of params.seededTemplates) {
    const existingRes = await params.supabase
      .from("playbooks")
      .select("id")
      .eq("org_id", params.orgId)
      .eq("playbook_type", seed.playbookType)
      .eq("title", seed.title)
      .limit(1)
      .maybeSingle();

    if (existingRes.error) throw new Error(existingRes.error.message);
    if (existingRes.data) {
      skippedCount += 1;
      continue;
    }

    const payload = asRecord(seed.payload);
    const entriesRaw = Array.isArray(payload.entries) ? payload.entries : [];

    const created = await createPlaybookWithEntries({
      supabase: params.supabase,
      orgId: params.orgId,
      scopeType: "org",
      ownerUserId: null,
      playbookType: seed.playbookType,
      title: seed.title,
      summary: seed.summary,
      status: "active",
      confidenceScore: 0.75,
      applicabilityNotes: `Template seeded from ${params.template.templateKey}`,
      sourceSnapshot: {
        seeded_from_template: true,
        template_key: params.template.templateKey,
        template_name: params.template.displayName,
        seed_template_id: seed.id,
        apply_mode: params.applyMode
      },
      generatedBy: params.actorUserId,
      entries: entriesRaw.map((item, idx) => {
        const row = asRecord(item);
        return {
          entryTitle: String(row.entry_title ?? `${seed.title} #${idx + 1}`),
          entrySummary: String(row.entry_summary ?? seed.summary),
          conditions: asRecord(row.conditions),
          recommendedActions: asStringArray(row.recommended_actions),
          cautionNotes: asStringArray(row.caution_notes),
          evidenceSnapshot: asRecord(row.evidence_snapshot),
          successSignal: asRecord(row.success_signal),
          failureModes: asStringArray(row.failure_modes),
          confidenceScore: typeof row.confidence_score === "number" ? Number(row.confidence_score) : 0.72,
          sortOrder: typeof row.sort_order === "number" ? Number(row.sort_order) : idx + 1
        };
      })
    });

    createdCount += 1;
    createdPlaybookIds.push(created.playbook.id);
  }

  return {
    createdCount,
    skippedCount,
    createdPlaybookIds
  };
}

