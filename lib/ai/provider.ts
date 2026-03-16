import { createDeepSeekProvider } from "@/lib/ai/providers/deepseek";
import type { AiProviderId, AiProviderRequest, AiProviderResponse } from "@/types/ai";

export interface AiProviderAdapter {
  id: AiProviderId;
  isConfigured(): boolean;
  getDefaultModel(opts?: { reasoning?: boolean }): string;
  getConfigStatus(): {
    provider: AiProviderId;
    configured: boolean;
    model: string;
    reasonerModel: string;
    strictModeEnabled: boolean;
    jsonModeEnabled: boolean;
    timeoutMs: number;
  };
  chatCompletion(request: AiProviderRequest): Promise<AiProviderResponse>;
}

function normalizeProvider(value: string | undefined): AiProviderId {
  if (value === "openai" || value === "qwen" || value === "zhipu") return value;
  return "deepseek";
}

export function getConfiguredAiProviderId(): AiProviderId {
  return normalizeProvider(process.env.AI_PROVIDER);
}

export function getAiProvider(): AiProviderAdapter {
  // Keep factory-based architecture for future providers.
  // Current production default: deepseek.
  const providerId = getConfiguredAiProviderId();
  if (providerId === "deepseek") {
    return createDeepSeekProvider();
  }
  // Graceful fallback for unsupported providers in this phase.
  return createDeepSeekProvider();
}

export function isRuleFallbackEnabled(): boolean {
  return (process.env.AI_FALLBACK_TO_RULE_ENGINE ?? "true").toLowerCase() !== "false";
}

