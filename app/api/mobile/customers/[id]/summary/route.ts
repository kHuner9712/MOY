import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { getMobileTouchpointView } from "@/services/mobile-touchpoint-service";

export async function GET(
  _request: Request,
  context: { params: { id: string } }
) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const customerId = context.params.id;

  try {
    const customerRes = await auth.supabase
      .from("customers")
      .select("id, owner_id, company_name, contact_name, current_stage, risk_level, next_followup_at, ai_summary, ai_suggestion, updated_at")
      .eq("org_id", auth.profile.org_id)
      .eq("id", customerId)
      .maybeSingle();
    if (customerRes.error) throw new Error(customerRes.error.message);
    const customer = customerRes.data as
      | {
          id: string;
          owner_id: string;
          company_name: string | null;
          contact_name: string | null;
          current_stage: string;
          risk_level: string;
          next_followup_at: string | null;
          ai_summary: string | null;
          ai_suggestion: string | null;
          updated_at: string;
        }
      | null;
    if (!customer) return fail("Customer not found", 404);
    if (auth.profile.role !== "manager" && customer.owner_id !== auth.profile.id) {
      return fail("No permission for this customer", 403);
    }

    const [followupsRes, prepRes, outcomesRes, dealRes, touchpoints] = await Promise.all([
      auth.supabase
        .from("followups")
        .select("id, communication_type, summary, next_step, next_followup_at, created_at, draft_status")
        .eq("org_id", auth.profile.org_id)
        .eq("customer_id", customerId)
        .order("created_at", { ascending: false })
        .limit(8),
      auth.supabase
        .from("prep_cards")
        .select("id, card_type, status, title, summary, updated_at")
        .eq("org_id", auth.profile.org_id)
        .eq("customer_id", customerId)
        .order("updated_at", { ascending: false })
        .limit(8),
      auth.supabase
        .from("action_outcomes")
        .select("id, outcome_type, result_status, key_outcome_summary, created_at")
        .eq("org_id", auth.profile.org_id)
        .eq("customer_id", customerId)
        .order("created_at", { ascending: false })
        .limit(8),
      auth.supabase
        .from("deal_rooms")
        .select("id, title, room_status, priority_band, manager_attention_needed, command_summary")
        .eq("org_id", auth.profile.org_id)
        .eq("customer_id", customerId)
        .order("updated_at", { ascending: false })
        .limit(1)
        .maybeSingle(),
      getMobileTouchpointView({
        supabase: auth.supabase,
        profile: auth.profile,
        customerId
      })
    ]);

    if (followupsRes.error) throw new Error(followupsRes.error.message);
    if (prepRes.error) throw new Error(prepRes.error.message);
    if (outcomesRes.error) throw new Error(outcomesRes.error.message);
    if (dealRes.error) throw new Error(dealRes.error.message);

    return ok({
      customer: {
        id: customer.id,
        companyName: customer.company_name,
        contactName: customer.contact_name,
        stage: customer.current_stage,
        riskLevel: customer.risk_level,
        nextFollowupAt: customer.next_followup_at,
        aiSummary: customer.ai_summary,
        aiSuggestion: customer.ai_suggestion,
        updatedAt: customer.updated_at
      },
      recentFollowups: followupsRes.data ?? [],
      recentPrepCards: prepRes.data ?? [],
      recentOutcomes: outcomesRes.data ?? [],
      dealRoom: dealRes.data ?? null,
      touchpoints
    });
  } catch (error) {
    return fail(error instanceof Error ? error.message : "mobile_customer_summary_failed", 500);
  }
}
