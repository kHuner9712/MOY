import { ok } from "@/lib/api-response";
import { createSupabaseServerClient } from "@/lib/supabase/server";
import { DEFAULT_MARKETING_PAGES } from "@/data/marketing-pages";
import { getMarketingPage } from "@/services/marketing-page-service";

export async function GET() {
  const supabase = createSupabaseServerClient();
  if (!supabase) {
    return ok({
      page: DEFAULT_MARKETING_PAGES.product
    });
  }

  const page =
    (await getMarketingPage({
      supabase,
      pageKey: "product"
    }).catch(() => null)) ?? DEFAULT_MARKETING_PAGES.product;

  return ok({
    page
  });
}
