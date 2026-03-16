import { createSupabaseBrowserClient } from "@/lib/supabase/client";
import { pickAutoCompletableTaskIdsAfterFollowup } from "@/lib/work-item-linkage";
import { mapFollowupRow } from "@/services/mappers";
import type { Database } from "@/types/database";
import type { FollowupCreateResult, FollowupInput, FollowupRecord } from "@/types/followup";

type ProfileAccessRow = Pick<Database["public"]["Tables"]["profiles"]["Row"], "id" | "org_id" | "role">;
type CustomerAccessRow = Pick<Database["public"]["Tables"]["customers"]["Row"], "id" | "owner_id" | "org_id">;

export const followupService = {
  async listAll(): Promise<FollowupRecord[]> {
    const supabase = createSupabaseBrowserClient();
    const { data, error } = await supabase
      .from("followups")
      .select("*, owner:profiles!followups_owner_id_fkey(id, display_name)")
      .order("created_at", { ascending: false });

    if (error) throw new Error(error.message);
    return (data ?? []).map((row) => mapFollowupRow(row as never));
  },

  async listByCustomerId(customerId: string): Promise<FollowupRecord[]> {
    const supabase = createSupabaseBrowserClient();
    const { data, error } = await supabase
      .from("followups")
      .select("*, owner:profiles!followups_owner_id_fkey(id, display_name)")
      .eq("customer_id", customerId)
      .order("created_at", { ascending: false });

    if (error) throw new Error(error.message);
    return (data ?? []).map((row) => mapFollowupRow(row as never));
  },

  async create(input: FollowupInput): Promise<FollowupCreateResult> {
    const supabase = createSupabaseBrowserClient();
    const {
      data: { user }
    } = await supabase.auth.getUser();
    if (!user) throw new Error("Please login before creating followup");

    const { data: profile, error: profileError } = await supabase.from("profiles").select("*").eq("id", user.id).maybeSingle();
    if (profileError || !profile) throw new Error("Failed to load current profile");
    const profileRow = profile as ProfileAccessRow;

    const { data: customerData, error: customerError } = await supabase
      .from("customers")
      .select("id, owner_id, org_id")
      .eq("id", input.customerId)
      .maybeSingle();

    if (customerError || !customerData) throw new Error("Customer not found");
    const customerRow = customerData as CustomerAccessRow;

    if (customerRow.org_id !== profileRow.org_id) {
      throw new Error("Cross-org customer access denied");
    }
    if (profileRow.role !== "manager" && customerRow.owner_id !== profileRow.id) {
      throw new Error("Sales can only create followup for owned customers");
    }

    const { data: inserted, error: insertError } = await supabase
      .from("followups")
      .insert(
        {
          org_id: profileRow.org_id,
          customer_id: input.customerId,
          owner_id: input.ownerId,
          communication_type: input.method,
          summary: input.summary,
          customer_needs: input.customerNeeds,
          objections: input.objections || null,
          next_step: input.nextPlan,
          next_followup_at: input.nextFollowupAt,
          needs_ai_analysis: input.needsAiAnalysis,
          source_input_id: input.sourceInputId ?? null,
          draft_status: input.draftStatus ?? "confirmed",
          ai_summary: null,
          ai_suggestion: null,
          ai_risk_level: null,
          ai_leak_risk: null,
          created_by: profileRow.id
        } as never
      )
      .select("*, owner:profiles!followups_owner_id_fkey(id, display_name)")
      .single();

    if (insertError || !inserted) throw new Error(insertError?.message ?? "Failed to create followup");
    const insertedRow = inserted as { id: string };

    // Auto close related task items after a real followup is created.
    try {
      const { data: linkedTasks, error: linkedTaskError } = await (supabase.from("work_items") as any)
        .select("*")
        .eq("org_id", profileRow.org_id)
        .eq("owner_id", profileRow.id)
        .eq("customer_id", input.customerId)
        .in("status", ["todo", "in_progress", "snoozed"])
        .in("work_type", ["followup_call", "review_customer"]);

      if (!linkedTaskError && Array.isArray(linkedTasks) && linkedTasks.length > 0) {
        const ids = pickAutoCompletableTaskIdsAfterFollowup({
          tasks: linkedTasks,
          customerId: input.customerId
        });
        if (ids.length > 0) {
          const now = new Date().toISOString();
          await (supabase.from("work_items") as any)
            .update({
              status: "done",
              completed_at: now,
              snoozed_until: null,
              updated_at: now
            })
            .in("id", ids);

          const logs = linkedTasks
            .filter((item: any) => ids.includes(item.id))
            .map((item: any) => ({
              org_id: profileRow.org_id,
              work_item_id: item.id,
              user_id: profileRow.id,
              action_type: "completed",
              action_note: `Auto completed after followup ${insertedRow.id}`,
              before_snapshot: {
                status: item.status,
                title: item.title
              },
              after_snapshot: {
                status: "done",
                followup_id: insertedRow.id
              }
            }));
          await (supabase.from("task_execution_logs") as any).insert(logs);
        }
      }
    } catch {
      // non-blocking
    }

    let analysisStatus: FollowupCreateResult["analysisStatus"] = "skipped";
    let analysisMessage = "Followup created successfully.";
    let aiRunId: string | null = null;

    if (input.needsAiAnalysis) {
      try {
        const response = await fetch("/api/ai/followup-analysis", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            customerId: input.customerId,
            followupId: insertedRow.id
          })
        });

        const payload = (await response.json()) as {
          success: boolean;
          data: { runId?: string; resultSource?: "provider" | "fallback" } | null;
          error: string | null;
        };

        if (response.ok && payload.success) {
          aiRunId = payload.data?.runId ?? null;
          if (payload.data?.resultSource === "fallback") {
            analysisStatus = "fallback";
            analysisMessage = "Followup saved; AI fell back to rule-based analysis.";
          } else {
            analysisStatus = "completed";
            analysisMessage = "Followup saved and AI analysis completed.";
          }
        } else {
          analysisStatus = "failed";
          analysisMessage = payload.error ?? "Followup saved but AI analysis failed.";
        }
      } catch {
        analysisStatus = "failed";
        analysisMessage = "Followup saved but AI analysis trigger failed.";
      }
    }

    // Best-effort closed-loop capture: followup submit should create an outcome trail.
    try {
      await fetch("/api/outcomes/capture", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          customerId: input.customerId,
          followupId: insertedRow.id,
          outcomeType: "followup_result",
          autoInfer: true,
          summaryHint: input.summary,
          usedPrepCard: false,
          usedDraft: false
        })
      });
    } catch {
      // non-blocking
    }

    return {
      followup: mapFollowupRow(inserted as never),
      analysisStatus,
      analysisMessage,
      aiRunId
    };
  }
};
