import { DEFAULT_MARKETING_PAGES } from "@/data/marketing-pages";
import { createSupabaseAdminClient, hasSupabaseAdminEnv } from "@/lib/supabase/admin";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { listIndustryTemplates } from "@/services/industry-template-service";
import type { IndustryTemplate } from "@/types/productization";
import type { MarketingPage, MarketingPageKey } from "@/types/commercialization";

type DbClient = ServerSupabaseClient;

interface MarketingPageRow {
  id: string;
  page_key: MarketingPageKey;
  status: MarketingPage["status"];
  title: string;
  subtitle: string;
  content_payload: Record<string, unknown> | null;
  seo_payload: Record<string, unknown> | null;
  updated_by: string | null;
  created_at: string;
  updated_at: string;
}

function asRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function mapMarketingPageRow(row: MarketingPageRow): MarketingPage {
  return {
    id: row.id,
    pageKey: row.page_key,
    status: row.status,
    title: row.title,
    subtitle: row.subtitle,
    contentPayload: asRecord(row.content_payload),
    seoPayload: asRecord(row.seo_payload),
    updatedBy: row.updated_by,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

async function getReadonlySupabaseClient(supabase?: DbClient): Promise<DbClient | null> {
  if (supabase) return supabase;
  if (!hasSupabaseAdminEnv()) return null;
  return createSupabaseAdminClient() as unknown as DbClient;
}

export async function getMarketingPage(params: {
  pageKey: MarketingPageKey;
  supabase?: DbClient;
  includeDraft?: boolean;
}): Promise<MarketingPage> {
  const client = await getReadonlySupabaseClient(params.supabase);
  if (!client) return DEFAULT_MARKETING_PAGES[params.pageKey];

  let query = (client as any).from("marketing_pages").select("*").eq("page_key", params.pageKey);
  if (!params.includeDraft) query = query.eq("status", "published");

  const res = await query.order("updated_at", { ascending: false }).limit(1).maybeSingle();
  if (res.error) return DEFAULT_MARKETING_PAGES[params.pageKey];
  if (!res.data) return DEFAULT_MARKETING_PAGES[params.pageKey];
  return mapMarketingPageRow(res.data as MarketingPageRow);
}

export async function listMarketingPages(params: {
  supabase?: DbClient;
  includeDraft?: boolean;
}): Promise<MarketingPage[]> {
  const client = await getReadonlySupabaseClient(params.supabase);
  if (!client) return Object.values(DEFAULT_MARKETING_PAGES);

  let query = (client as any).from("marketing_pages").select("*").order("page_key", { ascending: true });
  if (!params.includeDraft) query = query.eq("status", "published");

  const res = await query;
  if (res.error) return Object.values(DEFAULT_MARKETING_PAGES);
  const rows = (res.data ?? []) as MarketingPageRow[];
  if (rows.length === 0) return Object.values(DEFAULT_MARKETING_PAGES);
  return rows.map(mapMarketingPageRow);
}

export async function getPublicIndustriesCatalog(params?: {
  supabase?: DbClient;
}): Promise<{
  page: MarketingPage;
  templates: IndustryTemplate[];
}> {
  const page = await getMarketingPage({
    supabase: params?.supabase,
    pageKey: "industries"
  });

  const client = await getReadonlySupabaseClient(params?.supabase);
  if (!client) {
    return {
      page,
      templates: []
    };
  }

  const templates = await listIndustryTemplates({
    supabase: client
  });

  return {
    page,
    templates
  };
}
