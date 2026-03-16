import { createSupabaseBrowserClient } from "@/lib/supabase/client";
import { mapOpportunityRow } from "@/services/mappers";
import type { Opportunity, OpportunityStage } from "@/types/opportunity";

export const opportunityService = {
  async list(stage?: OpportunityStage | "all"): Promise<Opportunity[]> {
    const supabase = createSupabaseBrowserClient();
    let builder = supabase
      .from("opportunities")
      .select("*, owner:profiles!opportunities_owner_id_fkey(id, display_name), customer:customers!opportunities_customer_id_fkey(id, company_name)")
      .order("updated_at", { ascending: false });

    if (stage && stage !== "all") {
      builder = builder.eq("stage", stage);
    }

    const { data, error } = await builder;
    if (error) throw new Error(error.message);
    return (data ?? []).map((row) => mapOpportunityRow(row as never));
  },

  async listByOwner(ownerId: string): Promise<Opportunity[]> {
    const supabase = createSupabaseBrowserClient();
    const { data, error } = await supabase
      .from("opportunities")
      .select("*, owner:profiles!opportunities_owner_id_fkey(id, display_name), customer:customers!opportunities_customer_id_fkey(id, company_name)")
      .eq("owner_id", ownerId)
      .order("updated_at", { ascending: false });

    if (error) throw new Error(error.message);
    return (data ?? []).map((row) => mapOpportunityRow(row as never));
  }
};
