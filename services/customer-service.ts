import { createSupabaseBrowserClient } from "@/lib/supabase/client";
import { mapCustomerRow } from "@/services/mappers";
import type { Customer, CustomerStage, RiskLevel } from "@/types/customer";
import type { Database } from "@/types/database";

export interface CustomerQuery {
  search?: string;
  ownerId?: string;
  stage?: CustomerStage | "all";
  riskLevel?: RiskLevel | "all";
  sortBy?: "updatedAt" | "nextFollowupAt" | "winProbability";
  sortOrder?: "asc" | "desc";
}

type QuickCreateProfile = Pick<Database["public"]["Tables"]["profiles"]["Row"], "id" | "org_id" | "display_name">;

function mapSortField(sortBy: CustomerQuery["sortBy"]): keyof Database["public"]["Tables"]["customers"]["Row"] {
  if (sortBy === "nextFollowupAt") return "next_followup_at";
  if (sortBy === "winProbability") return "win_probability";
  return "updated_at";
}

function includesSearch(customer: Customer, search: string): boolean {
  const keyword = search.toLowerCase();
  return [customer.customerName, customer.companyName, customer.contactName, customer.phone, customer.email]
    .join("|")
    .toLowerCase()
    .includes(keyword);
}

function sortCustomers(items: Customer[], sortBy: CustomerQuery["sortBy"], order: CustomerQuery["sortOrder"]): Customer[] {
  const sorted = [...items];
  sorted.sort((a, b) => {
    let compare = 0;
    if (sortBy === "nextFollowupAt") {
      compare = new Date(a.nextFollowupAt).getTime() - new Date(b.nextFollowupAt).getTime();
    } else if (sortBy === "winProbability") {
      compare = a.winProbability - b.winProbability;
    } else {
      compare = new Date(a.updatedAt).getTime() - new Date(b.updatedAt).getTime();
    }
    return order === "asc" ? compare : -compare;
  });
  return sorted;
}

export function applyCustomerQuery(source: Customer[], query?: CustomerQuery): Customer[] {
  const normalizedQuery = query ?? {};
  let items = [...source];

  if (normalizedQuery.search?.trim()) {
    items = items.filter((customer) => includesSearch(customer, normalizedQuery.search!.trim()));
  }

  if (normalizedQuery.ownerId && normalizedQuery.ownerId !== "all") {
    items = items.filter((customer) => customer.ownerId === normalizedQuery.ownerId);
  }

  if (normalizedQuery.stage && normalizedQuery.stage !== "all") {
    items = items.filter((customer) => customer.stage === normalizedQuery.stage);
  }

  if (normalizedQuery.riskLevel && normalizedQuery.riskLevel !== "all") {
    items = items.filter((customer) => customer.riskLevel === normalizedQuery.riskLevel);
  }

  return sortCustomers(items, normalizedQuery.sortBy ?? "updatedAt", normalizedQuery.sortOrder ?? "desc");
}

export const customerService = {
  async listCustomers(query?: CustomerQuery): Promise<Customer[]> {
    const supabase = createSupabaseBrowserClient();
    const sortField = mapSortField(query?.sortBy);
    const ascending = query?.sortOrder === "asc";

    let builder = supabase
      .from("customers")
      .select("*, owner:profiles!customers_owner_id_fkey(id, display_name)")
      .order(sortField, { ascending, nullsFirst: false });

    if (query?.search?.trim()) {
      const keyword = query.search.trim().replaceAll("%", "");
      builder = builder.or(
        `name.ilike.%${keyword}%,company_name.ilike.%${keyword}%,contact_name.ilike.%${keyword}%,phone.ilike.%${keyword}%,email.ilike.%${keyword}%`
      );
    }

    if (query?.ownerId && query.ownerId !== "all") {
      builder = builder.eq("owner_id", query.ownerId);
    }

    if (query?.stage && query.stage !== "all") {
      builder = builder.eq("current_stage", query.stage);
    }

    if (query?.riskLevel && query.riskLevel !== "all") {
      builder = builder.eq("risk_level", query.riskLevel);
    }

    const { data, error } = await builder;
    if (error) throw new Error(error.message);
    return (data ?? []).map((row) => mapCustomerRow(row as never));
  },

  async getById(customerId: string): Promise<Customer | null> {
    const supabase = createSupabaseBrowserClient();
    const { data, error } = await supabase
      .from("customers")
      .select("*, owner:profiles!customers_owner_id_fkey(id, display_name)")
      .eq("id", customerId)
      .maybeSingle();

    if (error) throw new Error(error.message);
    if (!data) return null;
    return mapCustomerRow(data as never);
  },

  async updateAfterFollowup(params: {
    customerId: string;
    nextFollowupAt: string;
    aiSummary?: string;
    aiSuggestion?: string;
    aiRiskJudgement?: string;
    riskLevel?: RiskLevel;
  }): Promise<void> {
    const supabase = createSupabaseBrowserClient();
    const { error } = await supabase
      .from("customers")
      .update(
        {
          last_followup_at: new Date().toISOString(),
          next_followup_at: params.nextFollowupAt,
          ai_summary: params.aiSummary,
          ai_suggestion: params.aiSuggestion,
          ai_risk_judgement: params.aiRiskJudgement,
          risk_level: params.riskLevel,
          updated_at: new Date().toISOString()
        } as never
      )
      .eq("id", params.customerId);

    if (error) throw new Error(error.message);
  },

  async createQuickFromCapture(params: {
    companyName: string;
    contactName?: string;
    sourceChannel?: string;
    tags?: string[];
  }): Promise<Customer> {
    const supabase = createSupabaseBrowserClient();
    const {
      data: { user }
    } = await supabase.auth.getUser();
    if (!user) throw new Error("Please login first.");

    const { data: profileRaw, error: profileError } = await supabase.from("profiles").select("id, org_id, display_name").eq("id", user.id).maybeSingle();
    const profile = (profileRaw as QuickCreateProfile | null) ?? null;
    if (profileError || !profile) throw new Error(profileError?.message ?? "Failed to load profile");

    const normalizedCompany = params.companyName.trim();
    if (!normalizedCompany) throw new Error("Company name is required");

    const normalizedContact = params.contactName?.trim() || normalizedCompany;
    const insertPayload: Database["public"]["Tables"]["customers"]["Insert"] = {
      org_id: profile.org_id,
      owner_id: profile.id,
      name: normalizedContact,
      company_name: normalizedCompany,
      contact_name: normalizedContact,
      source_channel: params.sourceChannel ?? "capture_quick_create",
      current_stage: "lead",
      win_probability: 30,
      risk_level: "medium",
      tags: params.tags ?? [],
      has_decision_maker: false,
      created_by: profile.id
    };

    const { data: inserted, error: insertError } = await supabase
      .from("customers")
      // Relation selects can cause narrow inference in current Supabase typings.
      // Keep payload strongly typed and cast at callsite to avoid widening app types.
      .insert(insertPayload as never)
      .select("*, owner:profiles!customers_owner_id_fkey(id, display_name)")
      .single();

    if (insertError || !inserted) {
      throw new Error(insertError?.message ?? "Failed to create customer");
    }

    return mapCustomerRow(inserted as never);
  }
};
