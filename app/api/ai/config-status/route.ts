import { fail, ok } from "@/lib/api-response";
import { getAiProvider } from "@/lib/ai/provider";
import { getServerAuthContext } from "@/lib/server-auth";

export async function GET() {
  const auth = await getServerAuthContext();
  if (!auth) return fail("请先登录", 401);

  const provider = getAiProvider();
  const status = provider.getConfigStatus();

  return ok({
    provider: status.provider,
    configured: status.configured,
    model: status.model,
    reasonerModel: status.reasonerModel,
    strictModeEnabled: status.strictModeEnabled
  });
}

