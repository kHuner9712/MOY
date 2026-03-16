import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { listInboundLeads } from "@/services/inbound-lead-service";
import { canViewOrgUsage, getCurrentOrgMembership } from "@/services/org-membership-service";
import type { InboundLeadSource, InboundLeadStatus } from "@/types/commercialization";

const allowedStatuses: InboundLeadStatus[] = [
  "new",
  "qualified",
  "unqualified",
  "demo_scheduled",
  "trial_started",
  "converted_to_customer",
  "lost"
];

const allowedSources: InboundLeadSource[] = [
  "website_demo",
  "website_trial",
  "website_contact",
  "referral",
  "manual",
  "event",
  "content_download"
];

function parseEnumArray<T extends string>(values: string[], allowed: T[]): T[] {
  const set = new Set(allowed);
  return values.filter((item): item is T => set.has(item as T));
}

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const search = new URL(request.url).searchParams;
  const statuses = parseEnumArray(search.getAll("status"), allowedStatuses);
  const sources = parseEnumArray(search.getAll("source"), allowedSources);
  const ownerId = search.get("ownerId") ?? undefined;
  const limitRaw = Number(search.get("limit") ?? 80);
  const limit = Number.isFinite(limitRaw) ? Math.max(1, Math.min(300, Math.round(limitRaw))) : 80;

  try {
    const membership = await getCurrentOrgMembership({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });
    if (!membership || membership.seatStatus !== "active") return fail("forbidden", 403);

    const viewAll = canViewOrgUsage(membership.role);
    const leads = await listInboundLeads({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      ownerId: viewAll ? ownerId : auth.profile.id,
      statuses,
      sources,
      limit
    });

    return ok({
      leads
    });
  } catch (error) {
    return fail(error instanceof Error ? error.message : "list_growth_leads_failed", 500);
  }
}
