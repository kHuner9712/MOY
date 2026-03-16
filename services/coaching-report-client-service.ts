import { createSupabaseBrowserClient } from "@/lib/supabase/client";
import { mapCoachingReportRow } from "@/services/mappers";
import type { CoachingReport, CoachingReportScope, QualityPeriodType } from "@/types/quality";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

export const coachingReportClientService = {
  async list(params?: { scope?: CoachingReportScope; targetUserId?: string; limit?: number }): Promise<CoachingReport[]> {
    const supabase = createSupabaseBrowserClient();
    let query = supabase.from("coaching_reports").select("*").order("created_at", { ascending: false }).limit(params?.limit ?? 40);

    if (params?.scope) query = query.eq("report_scope", params.scope);
    if (params?.targetUserId) query = query.eq("target_user_id", params.targetUserId);

    const { data, error } = await query;
    if (error) throw new Error(error.message);
    return (data ?? []).map((item) => mapCoachingReportRow(item as never));
  },

  async generate(params: {
    scope: CoachingReportScope;
    periodType?: QualityPeriodType;
    targetUserId?: string | null;
  }): Promise<CoachingReport> {
    const response = await fetch("/api/reports/coaching-generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params)
    });

    const payload = (await response.json()) as ApiPayload<CoachingReport>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to generate coaching report");
    }
    return payload.data;
  }
};
