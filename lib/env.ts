import { z } from "zod";

import type { AiProviderId } from "@/types/ai";

const DEFAULT_AI_PROVIDER: AiProviderId = "deepseek";
const DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com";
const DEFAULT_DEEPSEEK_MODEL = "deepseek-chat";
const DEFAULT_DEEPSEEK_REASONER_MODEL = "deepseek-reasoner";
const DEFAULT_AI_TIMEOUT_MS = 30000;

const aiProviderSchema = z.enum(["deepseek", "openai", "qwen", "zhipu"]);

const nonEmptyString = (key: string) =>
  z
    .string({ required_error: `${key} is required` })
    .trim()
    .min(1, `${key} is required`);

const supabasePublicSchema = z.object({
  NEXT_PUBLIC_SUPABASE_URL: nonEmptyString("NEXT_PUBLIC_SUPABASE_URL"),
  NEXT_PUBLIC_SUPABASE_ANON_KEY: nonEmptyString("NEXT_PUBLIC_SUPABASE_ANON_KEY")
});

const supabaseServiceSchema = z.object({
  NEXT_PUBLIC_SUPABASE_URL: nonEmptyString("NEXT_PUBLIC_SUPABASE_URL"),
  SUPABASE_SERVICE_ROLE_KEY: nonEmptyString("SUPABASE_SERVICE_ROLE_KEY")
});

const selfSalesSchema = z.object({
  SELF_SALES_ORG_ID: nonEmptyString("SELF_SALES_ORG_ID")
});

const aiBaseSchema = z.object({
  AI_PROVIDER: aiProviderSchema.default(DEFAULT_AI_PROVIDER),
  AI_FALLBACK_TO_RULE_ENGINE: z.string().optional(),
  AI_ANALYSIS_TIMEOUT_MS: z.string().optional(),
  DEEPSEEK_API_KEY: z.string().optional(),
  DEEPSEEK_BASE_URL: z.string().optional(),
  DEEPSEEK_MODEL: z.string().optional(),
  DEEPSEEK_REASONER_MODEL: z.string().optional(),
  DEEPSEEK_STRICT_BETA_ENABLED: z.string().optional(),
  DEEPSEEK_JSON_MODE_ENABLED: z.string().optional(),
  DEEPSEEK_MAX_TOKENS: z.string().optional(),
  DEEPSEEK_TEMPERATURE: z.string().optional()
});

function readEnv(name: string): string | undefined {
  const value = process.env[name];
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

function toBoolean(value: string | undefined, fallback: boolean): boolean {
  if (!value) return fallback;
  const normalized = value.toLowerCase();
  if (normalized === "true") return true;
  if (normalized === "false") return false;
  return fallback;
}

function toNullableNumber(value: string | undefined): number | null {
  if (!value) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function toPositiveInt(value: string | undefined, fallback: number): number {
  if (!value) return fallback;
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
  return Math.floor(parsed);
}

function formatIssues(error: z.ZodError): string {
  return error.issues
    .map((issue) => {
      const key = issue.path.join(".") || "env";
      if (issue.code === "invalid_type" && issue.received === "undefined") {
        return `${key} is required`;
      }
      if (issue.code === "too_small") {
        return `${key} is required`;
      }
      return `${key}: ${issue.message}`;
    })
    .join("; ");
}

function parseOrThrow<T>(params: {
  schema: z.ZodType<T>;
  payload: Record<string, unknown>;
  scope: string;
}): T {
  const parsed = params.schema.safeParse(params.payload);
  if (parsed.success) return parsed.data;
  throw new Error(`[env:${params.scope}] Missing or invalid environment variables: ${formatIssues(parsed.error)}`);
}

export interface SupabasePublicEnv {
  url: string;
  anonKey: string;
}

export interface SupabaseServiceEnv {
  url: string;
  serviceRoleKey: string;
}

export interface AiRuntimeEnv {
  provider: AiProviderId;
  fallbackToRuleEngine: boolean;
  analysisTimeoutMs: number;
  deepseekApiKey: string | null;
  deepseekBaseUrl: string;
  deepseekModel: string;
  deepseekReasonerModel: string;
  deepseekStrictBetaEnabled: boolean;
  deepseekJsonModeEnabled: boolean;
  deepseekMaxTokens: number | null;
  deepseekTemperature: number | null;
}

export function hasSupabasePublicEnv(): boolean {
  return supabasePublicSchema.safeParse({
    NEXT_PUBLIC_SUPABASE_URL: readEnv("NEXT_PUBLIC_SUPABASE_URL"),
    NEXT_PUBLIC_SUPABASE_ANON_KEY: readEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
  }).success;
}

export function getSupabasePublicEnvOrThrow(scope = "supabase_public"): SupabasePublicEnv {
  const env = parseOrThrow({
    schema: supabasePublicSchema,
    payload: {
      NEXT_PUBLIC_SUPABASE_URL: readEnv("NEXT_PUBLIC_SUPABASE_URL"),
      NEXT_PUBLIC_SUPABASE_ANON_KEY: readEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
    },
    scope
  });

  return {
    url: env.NEXT_PUBLIC_SUPABASE_URL,
    anonKey: env.NEXT_PUBLIC_SUPABASE_ANON_KEY
  };
}

export function hasSupabaseServiceEnv(): boolean {
  return supabaseServiceSchema.safeParse({
    NEXT_PUBLIC_SUPABASE_URL: readEnv("NEXT_PUBLIC_SUPABASE_URL"),
    SUPABASE_SERVICE_ROLE_KEY: readEnv("SUPABASE_SERVICE_ROLE_KEY")
  }).success;
}

export function getSupabaseServiceEnvOrThrow(scope = "supabase_service"): SupabaseServiceEnv {
  const env = parseOrThrow({
    schema: supabaseServiceSchema,
    payload: {
      NEXT_PUBLIC_SUPABASE_URL: readEnv("NEXT_PUBLIC_SUPABASE_URL"),
      SUPABASE_SERVICE_ROLE_KEY: readEnv("SUPABASE_SERVICE_ROLE_KEY")
    },
    scope
  });

  return {
    url: env.NEXT_PUBLIC_SUPABASE_URL,
    serviceRoleKey: env.SUPABASE_SERVICE_ROLE_KEY
  };
}

export function getAiRuntimeEnv(scope = "ai_runtime"): AiRuntimeEnv {
  const env = parseOrThrow({
    schema: aiBaseSchema,
    payload: {
      AI_PROVIDER: readEnv("AI_PROVIDER"),
      AI_FALLBACK_TO_RULE_ENGINE: readEnv("AI_FALLBACK_TO_RULE_ENGINE"),
      AI_ANALYSIS_TIMEOUT_MS: readEnv("AI_ANALYSIS_TIMEOUT_MS"),
      DEEPSEEK_API_KEY: readEnv("DEEPSEEK_API_KEY"),
      DEEPSEEK_BASE_URL: readEnv("DEEPSEEK_BASE_URL"),
      DEEPSEEK_MODEL: readEnv("DEEPSEEK_MODEL"),
      DEEPSEEK_REASONER_MODEL: readEnv("DEEPSEEK_REASONER_MODEL"),
      DEEPSEEK_STRICT_BETA_ENABLED: readEnv("DEEPSEEK_STRICT_BETA_ENABLED"),
      DEEPSEEK_JSON_MODE_ENABLED: readEnv("DEEPSEEK_JSON_MODE_ENABLED"),
      DEEPSEEK_MAX_TOKENS: readEnv("DEEPSEEK_MAX_TOKENS"),
      DEEPSEEK_TEMPERATURE: readEnv("DEEPSEEK_TEMPERATURE")
    },
    scope
  });

  return {
    provider: (env.AI_PROVIDER ?? DEFAULT_AI_PROVIDER) as AiProviderId,
    fallbackToRuleEngine: toBoolean(env.AI_FALLBACK_TO_RULE_ENGINE, true),
    analysisTimeoutMs: toPositiveInt(env.AI_ANALYSIS_TIMEOUT_MS, DEFAULT_AI_TIMEOUT_MS),
    deepseekApiKey: env.DEEPSEEK_API_KEY ?? null,
    deepseekBaseUrl: env.DEEPSEEK_BASE_URL ?? DEFAULT_DEEPSEEK_BASE_URL,
    deepseekModel: env.DEEPSEEK_MODEL ?? DEFAULT_DEEPSEEK_MODEL,
    deepseekReasonerModel: env.DEEPSEEK_REASONER_MODEL ?? DEFAULT_DEEPSEEK_REASONER_MODEL,
    deepseekStrictBetaEnabled: toBoolean(env.DEEPSEEK_STRICT_BETA_ENABLED, false),
    deepseekJsonModeEnabled: toBoolean(env.DEEPSEEK_JSON_MODE_ENABLED, true),
    deepseekMaxTokens: toNullableNumber(env.DEEPSEEK_MAX_TOKENS),
    deepseekTemperature: toNullableNumber(env.DEEPSEEK_TEMPERATURE)
  };
}

export function hasDeepSeekApiKey(): boolean {
  return Boolean(readEnv("DEEPSEEK_API_KEY"));
}

export function assertDeepSeekApiKey(scope = "ai_deepseek"): string {
  const key = readEnv("DEEPSEEK_API_KEY");
  if (!key) {
    throw new Error(`[env:${scope}] Missing required environment variable: DEEPSEEK_API_KEY (required when using deepseek provider).`);
  }
  return key;
}

export function requireSelfSalesOrgIdEnv(scope = "self_sales"): string {
  const env = parseOrThrow({
    schema: selfSalesSchema,
    payload: {
      SELF_SALES_ORG_ID: readEnv("SELF_SALES_ORG_ID")
    },
    scope
  });
  return env.SELF_SALES_ORG_ID;
}
