import { createDeepSeekProvider } from "@/lib/ai/providers/deepseek";
import { getAiRuntimeEnv } from "@/lib/env";
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

export function getConfiguredAiProviderId(): AiProviderId {
  return getAiRuntimeEnv("ai_provider_selection").provider;
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
  return getAiRuntimeEnv("ai_fallback_mode").fallbackToRuleEngine;
}
