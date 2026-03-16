import type { ServerSupabaseClient } from "@/lib/supabase/types";
import type { Database } from "@/types/database";

type DbClient = ServerSupabaseClient;
type CustomerRow = Database["public"]["Tables"]["customers"]["Row"];

export interface CustomerMatchCandidate {
  id: string;
  name: string;
  companyName: string;
  contactName: string;
  ownerId: string;
  score: number;
}

export interface CustomerMatchResult {
  matchedCustomer: CustomerRow | null;
  confidence: number;
  candidates: CustomerMatchCandidate[];
}

function normalizeText(value: string | null | undefined): string {
  if (!value) return "";
  return value.replace(/\s+/g, "").toLowerCase();
}

function containsEither(a: string, b: string): boolean {
  if (!a || !b) return false;
  return a.includes(b) || b.includes(a);
}

export function scoreCustomerCandidate(params: {
  hint: string;
  customerName: string;
  companyName: string;
  contactName: string;
  ownerBoost?: boolean;
  recentBoost?: boolean;
}): number {
  const hint = normalizeText(params.hint);
  if (!hint) return 0;

  const customerName = normalizeText(params.customerName);
  const companyName = normalizeText(params.companyName);
  const contactName = normalizeText(params.contactName);

  let score = 0;

  if (customerName === hint || companyName === hint || contactName === hint) {
    score += 0.7;
  }

  if (containsEither(customerName, hint)) score += 0.35;
  if (containsEither(companyName, hint)) score += 0.35;
  if (containsEither(contactName, hint)) score += 0.25;

  if (params.ownerBoost) score += 0.08;
  if (params.recentBoost) score += 0.05;

  return Math.max(0, Math.min(1, score));
}

export function pickBestCustomerMatch(candidates: CustomerMatchCandidate[]): {
  best: CustomerMatchCandidate | null;
  confidence: number;
} {
  const sorted = [...candidates].sort((a, b) => b.score - a.score);
  const best = sorted[0] ?? null;
  if (!best) return { best: null, confidence: 0 };

  const second = sorted[1];
  const gap = second ? best.score - second.score : best.score;
  const confidence = Math.max(0, Math.min(1, best.score + Math.max(0, gap) * 0.2));

  return {
    best,
    confidence
  };
}

export async function matchCustomer(params: {
  supabase: DbClient;
  orgId: string;
  actorId: string;
  actorRole: "sales" | "manager";
  explicitCustomerId?: string | null;
  matchedCustomerName?: string | null;
  limit?: number;
}): Promise<CustomerMatchResult> {
  const limit = params.limit ?? 40;

  if (params.explicitCustomerId) {
    const { data, error } = await params.supabase
      .from("customers")
      .select("*")
      .eq("org_id", params.orgId)
      .eq("id", params.explicitCustomerId)
      .maybeSingle();

    if (error) throw new Error(error.message);
    const row = (data ?? null) as CustomerRow | null;

    if (!row) {
      return {
        matchedCustomer: null,
        confidence: 0,
        candidates: []
      };
    }

    return {
      matchedCustomer: row,
      confidence: 1,
      candidates: [
        {
          id: row.id,
          name: row.name,
          companyName: row.company_name,
          contactName: row.contact_name,
          ownerId: row.owner_id,
          score: 1
        }
      ]
    };
  }

  const hint = normalizeText(params.matchedCustomerName);
  if (!hint) {
    return {
      matchedCustomer: null,
      confidence: 0,
      candidates: []
    };
  }

  let query = params.supabase.from("customers").select("*").eq("org_id", params.orgId).order("updated_at", { ascending: false }).limit(limit);
  if (params.actorRole !== "manager") {
    query = query.eq("owner_id", params.actorId);
  }

  const { data, error } = await query;
  if (error) throw new Error(error.message);

  const rows = (data ?? []) as CustomerRow[];
  const candidates: CustomerMatchCandidate[] = rows
    .map((row) => {
      const recentBoost = row.last_followup_at ? Date.now() - new Date(row.last_followup_at).getTime() < 14 * 24 * 60 * 60 * 1000 : false;
      const score = scoreCustomerCandidate({
        hint,
        customerName: row.name,
        companyName: row.company_name,
        contactName: row.contact_name,
        ownerBoost: row.owner_id === params.actorId,
        recentBoost
      });

      return {
        id: row.id,
        name: row.name,
        companyName: row.company_name,
        contactName: row.contact_name,
        ownerId: row.owner_id,
        score
      };
    })
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, 6);

  const { best, confidence } = pickBestCustomerMatch(candidates);

  if (!best) {
    return {
      matchedCustomer: null,
      confidence: 0,
      candidates
    };
  }

  const matchedRow = rows.find((row) => row.id === best.id) ?? null;

  return {
    matchedCustomer: matchedRow,
    confidence,
    candidates
  };
}
