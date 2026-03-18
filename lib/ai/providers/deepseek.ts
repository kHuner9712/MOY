import { assertDeepSeekApiKey, getAiRuntimeEnv } from "../../env";
import type { AiProviderRequest, AiProviderResponse, AiToolDefinition } from "@/types/ai";

const DEFAULT_BASE_URL = "https://api.deepseek.com";
const DEFAULT_MODEL = "deepseek-chat";
const DEFAULT_REASONER_MODEL = "deepseek-reasoner";
const DEFAULT_TIMEOUT_MS = 30000;

interface DeepSeekConfig {
  apiKey: string | null;
  baseUrl: string;
  model: string;
  reasonerModel: string;
  strictBetaEnabled: boolean;
  jsonModeEnabled: boolean;
  timeoutMs: number;
  maxTokens: number | null;
  temperature: number | null;
}

function normalizeBaseUrl(baseUrl: string): string {
  return baseUrl.endsWith("/") ? baseUrl.slice(0, -1) : baseUrl;
}

function getConfig(): DeepSeekConfig {
  const env = getAiRuntimeEnv("deepseek_provider_config");
  return {
    apiKey: env.deepseekApiKey,
    baseUrl: normalizeBaseUrl(env.deepseekBaseUrl ?? DEFAULT_BASE_URL),
    model: env.deepseekModel ?? DEFAULT_MODEL,
    reasonerModel: env.deepseekReasonerModel ?? DEFAULT_REASONER_MODEL,
    strictBetaEnabled: env.deepseekStrictBetaEnabled,
    jsonModeEnabled: env.deepseekJsonModeEnabled,
    timeoutMs: env.analysisTimeoutMs > 0 ? env.analysisTimeoutMs : DEFAULT_TIMEOUT_MS,
    maxTokens: env.deepseekMaxTokens,
    temperature: env.deepseekTemperature
  };
}

function getEndpoint(baseUrl: string, strictMode: boolean): string {
  if (strictMode) return `${baseUrl}/beta/chat/completions`;
  return `${baseUrl}/chat/completions`;
}

export function normalizeDeepSeekContent(content: unknown): string | null {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    const text = content
      .map((part) => {
        if (typeof part === "string") return part;
        if (part && typeof part === "object" && "text" in part && typeof (part as { text?: unknown }).text === "string") {
          return (part as { text: string }).text;
        }
        return "";
      })
      .join("")
      .trim();
    return text || null;
  }
  return null;
}

export function parseDeepSeekJsonText(rawText: string | null): Record<string, unknown> | null {
  if (!rawText) return null;
  try {
    const parsed = JSON.parse(rawText);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    return parsed as Record<string, unknown>;
  } catch {
    return null;
  }
}

function toStrictTools(tools: AiToolDefinition[] | undefined, strictMode: boolean): Record<string, unknown>[] | undefined {
  if (!tools || tools.length === 0) return undefined;
  return tools.map((tool) => ({
    type: "function",
    function: {
      name: tool.function.name,
      description: tool.function.description,
      parameters: tool.function.parameters,
      strict: strictMode ? true : tool.function.strict ?? false
    }
  }));
}

async function callDeepSeek(params: {
  config: DeepSeekConfig;
  request: AiProviderRequest;
  strictMode: boolean;
}): Promise<AiProviderResponse> {
  const { config, request, strictMode } = params;
  const startedAt = Date.now();
  const endpoint = getEndpoint(config.baseUrl, strictMode);
  const model = request.model || (request.useReasonerModel ? config.reasonerModel : config.model);
  const timeoutMs = request.timeoutMs ?? config.timeoutMs;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort("timeout"), timeoutMs);

  const payload: Record<string, unknown> = {
    model,
    messages: [
      { role: "system", content: request.systemPrompt },
      { role: "system", content: request.developerPrompt },
      { role: "user", content: request.userPrompt }
    ],
    temperature: request.temperature ?? config.temperature ?? 0.2
  };

  const maxTokens = request.maxTokens ?? config.maxTokens;
  if (typeof maxTokens === "number") {
    payload.max_tokens = maxTokens;
  }

  if (request.jsonMode ?? config.jsonModeEnabled) {
    payload.response_format = { type: "json_object" };
  }

  const strictTools = toStrictTools(request.tools, strictMode);
  if (strictTools) {
    payload.tools = strictTools;
    payload.tool_choice = "auto";
  }

  try {
    if (!config.apiKey) {
      assertDeepSeekApiKey("deepseek_provider_call");
    }

    const response = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${config.apiKey}`
      },
      body: JSON.stringify(payload),
      signal: controller.signal
    });

    const raw = (await response.json().catch(() => null)) as Record<string, unknown> | null;
    const latencyMs = Date.now() - startedAt;

    if (!response.ok || !raw) {
      const errText =
        (raw?.error && typeof raw.error === "object" && raw.error && "message" in raw.error
          ? String((raw.error as { message?: unknown }).message ?? "")
          : "") || `DeepSeek request failed with status ${response.status}`;
      return {
        provider: "deepseek",
        model,
        latencyMs,
        finishReason: null,
        usage: null,
        rawResponse: raw ?? {},
        rawText: null,
        parsedJson: null,
        error: errText
      };
    }

    const choices = Array.isArray(raw.choices) ? raw.choices : [];
    const first = choices[0] as Record<string, unknown> | undefined;
    const message = first?.message as Record<string, unknown> | undefined;
    const rawText = normalizeDeepSeekContent(message?.content);
    const parsedJson = parseDeepSeekJsonText(rawText);

    const usage = (raw.usage ?? null) as Record<string, unknown> | null;
    return {
      provider: "deepseek",
      model: typeof raw.model === "string" ? raw.model : model,
      latencyMs,
      finishReason: typeof first?.finish_reason === "string" ? (first.finish_reason as string) : null,
      usage: usage
        ? {
            promptTokens: typeof usage.prompt_tokens === "number" ? usage.prompt_tokens : null,
            completionTokens: typeof usage.completion_tokens === "number" ? usage.completion_tokens : null,
            totalTokens: typeof usage.total_tokens === "number" ? usage.total_tokens : null
          }
        : null,
      rawResponse: raw,
      rawText,
      parsedJson,
      error: null
    };
  } catch (error) {
    const latencyMs = Date.now() - startedAt;
    const errorMessage =
      error instanceof Error
        ? error.name === "AbortError"
          ? `DeepSeek request timeout (${timeoutMs}ms)`
          : error.message
        : "Unknown deepseek error";

    return {
      provider: "deepseek",
      model,
      latencyMs,
      finishReason: null,
      usage: null,
      rawResponse: {},
      rawText: null,
      parsedJson: null,
      error: errorMessage
    };
  } finally {
    clearTimeout(timeout);
  }
}

export function createDeepSeekProvider() {
  const config = getConfig();
  return {
    id: "deepseek" as const,

    isConfigured(): boolean {
      return Boolean(config.apiKey);
    },

    getDefaultModel(opts?: { reasoning?: boolean }): string {
      if (opts?.reasoning) return config.reasonerModel;
      return config.model;
    },

    getConfigStatus() {
      return {
        provider: "deepseek" as const,
        configured: Boolean(config.apiKey),
        model: config.model,
        reasonerModel: config.reasonerModel,
        strictModeEnabled: config.strictBetaEnabled,
        jsonModeEnabled: config.jsonModeEnabled,
        timeoutMs: config.timeoutMs
      };
    },

    async chatCompletion(request: AiProviderRequest): Promise<AiProviderResponse> {
      const strictRequested = request.strictMode === true && config.strictBetaEnabled;
      if (!strictRequested) {
        return callDeepSeek({
          config,
          request,
          strictMode: false
        });
      }

      const strictResult = await callDeepSeek({
        config,
        request,
        strictMode: true
      });

      // Strict mode fallback: retry non-strict once.
      if (!strictResult.error) {
        return {
          ...strictResult,
          strictFallbackUsed: false
        };
      }

      const fallbackResult = await callDeepSeek({
        config,
        request,
        strictMode: false
      });

      if (!fallbackResult.error) {
        return {
          ...fallbackResult,
          strictFallbackUsed: true
        };
      }

      return {
        ...fallbackResult,
        strictFallbackUsed: true,
        error: `strict_failed: ${strictResult.error}; fallback_failed: ${fallbackResult.error}`
      };
    }
  };
}
