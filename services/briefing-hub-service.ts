import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { buildBriefingHubView } from "@/lib/briefing-hub";
import { mapContentDraftRow, mapMorningBriefRow, mapPrepCardRow } from "@/services/mappers";
import type { Database } from "@/types/database";
import type { BriefingHubView } from "@/types/preparation";

type DbClient = ServerSupabaseClient;
type ProfileRow = Database["public"]["Tables"]["profiles"]["Row"];

function todayDate(): string {
  return new Date().toISOString().slice(0, 10);
}

export async function getBriefingHubData(params: {
  supabase: DbClient;
  profile: ProfileRow;
  date?: string;
  prepLimit?: number;
  draftLimit?: number;
}): Promise<BriefingHubView> {
  const date = params.date ?? todayDate();
  const isManager = params.profile.role === "manager";
  const draftBaseQuery = params.supabase
    .from("content_drafts")
    .select("*")
    .eq("org_id", params.profile.org_id)
    .order("created_at", { ascending: false })
    .limit(params.draftLimit ?? 40);

  const [briefRes, prepRes, draftRes] = await Promise.all([
    params.supabase
      .from("morning_briefs")
      .select("*")
      .eq("org_id", params.profile.org_id)
      .eq("target_user_id", params.profile.id)
      .eq("brief_date", date)
      .order("created_at", { ascending: false })
      .limit(1)
      .maybeSingle(),
    isManager
      ? params.supabase
          .from("prep_cards")
          .select("*")
          .eq("org_id", params.profile.org_id)
          .in("status", ["draft", "ready", "stale"])
          .order("created_at", { ascending: false })
          .limit(params.prepLimit ?? 40)
      : params.supabase
          .from("prep_cards")
          .select("*")
          .eq("org_id", params.profile.org_id)
          .eq("owner_id", params.profile.id)
          .in("status", ["draft", "ready", "stale"])
          .order("created_at", { ascending: false })
          .limit(params.prepLimit ?? 40),
    isManager
      ? draftBaseQuery.in("draft_type", ["manager_checkin_note", "internal_update"])
      : draftBaseQuery.eq("owner_id", params.profile.id)
  ]);

  if (briefRes.error) throw new Error(briefRes.error.message);
  if (prepRes.error) throw new Error(prepRes.error.message);
  if (draftRes.error) throw new Error(draftRes.error.message);

  const prepRows = (prepRes.data ?? []) as Database["public"]["Tables"]["prep_cards"]["Row"][];
  const draftRows = (draftRes.data ?? []) as Database["public"]["Tables"]["content_drafts"]["Row"][];

  return buildBriefingHubView({
    morningBrief: briefRes.data ? mapMorningBriefRow(briefRes.data as Database["public"]["Tables"]["morning_briefs"]["Row"]) : null,
    prepCards: prepRows.map((item) => mapPrepCardRow(item)),
    contentDrafts: draftRows.map((item) => mapContentDraftRow(item))
  });
}
