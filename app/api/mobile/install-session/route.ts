import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { recordMobileInstallSession } from "@/services/mobile-shell-service";

const schema = z.object({
  deviceLabel: z.string().min(1).max(120),
  installType: z.enum(["browser", "pwa"]).default("browser"),
  appVersion: z.string().optional(),
  pushCapable: z.boolean().optional(),
  metadata: z.record(z.unknown()).optional()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = schema.safeParse(body);
  if (!parsed.success) return fail("Invalid payload", 400);

  try {
    const session = await recordMobileInstallSession({
      supabase: auth.supabase,
      profile: auth.profile,
      deviceLabel: parsed.data.deviceLabel,
      installType: parsed.data.installType,
      appVersion: parsed.data.appVersion ?? null,
      pushCapable: parsed.data.pushCapable ?? false,
      metadata: parsed.data.metadata ?? {}
    });
    return ok({ session });
  } catch (error) {
    return fail(error instanceof Error ? error.message : "mobile_install_session_failed", 500);
  }
}
