/**
 * 统一登录的纯逻辑层 —— 公开客户端 + 浏览器内 PKCE 直连。
 *
 * 为什么单独抽一层（且**不 import React、不碰任何存储**）：
 * 1. 登录是唯一的安全关键路径，必须能被门禁脚本（`scripts/check-oidc-auth.mjs`）
 *    直接 `import()` 后逐条断言，而不是靠人工点页面；
 * 2. 存储（localStorage / sessionStorage）、路由、提示属于「副作用」，
 *    放在 `session-store.ts` 与页面组件里，本文件只做「算」与「判」。
 *
 * 冻结的流程（不得擅自改动）：
 *   生成 verifier/state → 存待完成登录（TTL 10 分钟）→ 跳授权页
 *   → 回来先校验 state（再兑换）→ 用 verifier 换会话 → 交给调用方落库
 *
 * 红线：
 * - PKCE 只允许 S256；`plain` 等于没有 PKCE；
 * - **永远不带客户端密钥**：这是公开客户端，任何 `client_secret` 字段都是缺陷；
 * - 校验未通过时**不得**发起兑换（否则一次伪造跳转就能换出会话）；
 * - 本文件不记录任何日志（令牌与授权码一律不落日志）。
 */

/** 待完成登录的最长保留时长（毫秒）：超过即作废，用户需重新发起 */
export const PENDING_TTL_MS = 10 * 60 * 1000;

/**
 * 随机串字节数：32 字节 → base64url 43 字符。
 *
 * 43 字符正好落在 RFC 7636 对 `code_verifier` 规定的 43~128 区间下沿，
 * 且 32 字节（256 bit）熵足以让挑战值无法被逆推。
 */
export const RANDOM_BYTE_LENGTH = 32;

/** 本地时钟与身份服务的容差：提前 30 秒判定过期，避免「刚拿到就失效」 */
const CLOCK_SKEW_MS = 30_000;

/** 身份服务未下发有效期时的保守默认值（秒） */
const DEFAULT_LIFETIME_SECONDS = 300;

/** 有效期的上限（秒）：防止异常大值把会话变成事实上的「永不过期」 */
const MAX_LIFETIME_SECONDS = 12 * 60 * 60;

/** 环境不支持安全随机 / 摘要算法时的业务语言提示（不出现任何技术词） */
const UNSUPPORTED_ENVIRONMENT_MESSAGE = '当前环境无法安全登录，请更换浏览器后重试';

/** PKCE 挑战方法：本轮只允许 S256 */
export type CodeChallengeMethod = 'S256';

/** PKCE 对（verifier 只留在本地，challenge 才会出现在授权地址里） */
export interface PkcePair {
  /** 随机串（43 字符 base64url），回跳时用于兑换 */
  codeVerifier: string;
  /** 摘要值（43 字符 base64url），跳转时带给身份服务 */
  codeChallenge: string;
  /** 挑战方法，恒为 S256 */
  codeChallengeMethod: CodeChallengeMethod;
}

/** 一次尚未完成的登录：跳转前写入，回跳后校验并立即删除 */
export interface PendingAuthorization {
  /** 一次性随机值，回跳时必须逐字相等 */
  state: string;
  /** 与本次挑战配对的随机串 */
  codeVerifier: string;
  /** 发起时使用的回跳地址（兑换时必须与授权时完全一致） */
  redirectUri: string;
  /** 创建时刻（毫秒时间戳），用于 TTL 判定 */
  createdAt: number;
  /**
   * 登录成功后要回到的站内路径（深链场景）。
   *
   * 为什么放在这里而不是另开一个存储键：本对象本来就是「这一次登录是我发起的」
   * 的一次性载体，`returnTo` 与它同生命周期——一起写入、一起消费、一起作废，
   * 少一个键就少一处清理责任。
   *
   * 可选且**不参与** `isPendingAuthorization` 的必填校验：升级前写下的旧对象
   * 没有这个字段，若校验加严会让正在进行的登录被判为「本地状态不可信」。
   * 取值必须经 `lib/redirect.ts` 的 `resolveNextPath` 校验后才可用于跳转。
   */
  returnTo?: string;
}

/**
 * 登录成功后的会话数据。
 *
 * 字段名沿用身份服务的传输口径（snake_case）：兑换响应本来就是这个形状，
 * 原样保存可以少一层映射——映射是登录链路上最容易写错字段名的地方。
 */
export interface OidcSession {
  /** 访问令牌 */
  access_token: string;
  /** 续期用的令牌；本轮不自动续期，但先存下来供后续使用 */
  refresh_token: string | null;
  /** 过期时刻（毫秒时间戳，已扣除容差） */
  expires_at: number;
}

/** 授权地址的拼装参数 */
export interface AuthorizeUrlOptions {
  /** 身份服务地址（允许带结尾斜杠） */
  issuer: string;
  /** 公开客户端标识 */
  clientId: string;
  /** 回跳地址（绝对地址，必须与身份服务登记的一致） */
  redirectUri: string;
  /** 一次性随机值 */
  state: string;
  /** S256 挑战值 */
  codeChallenge: string;
  /** 授权范围 */
  scope: string;
}

/** 回跳参数不合格的具体原因（只用于内部分支，绝不直接展示给用户） */
export type CallbackErrorReason = 'provider-error' | 'missing-code' | 'missing-state';

/** 回跳参数的解析结果（判别联合：调用方必须处理全部分支） */
export type CallbackParseResult =
  | { kind: 'ok'; code: string; state: string }
  | { kind: 'error'; reason: CallbackErrorReason }
  | { kind: 'missing' };

/**
 * 兑换被拒的原因（只用于内部分支，绝不直接展示给用户）：
 * `callback` = 回跳参数不合格、`no-pending` = 本地没有待完成登录、`expired` = 超过 10 分钟、
 * `mismatch` = 一次性随机值不匹配、`exchange` = 兑换请求未成功。
 *
 * 取值刻意保持简短：联合类型一旦落在接近 100 列的位置，Prettier 3.3 与 3.9 的换行策略
 * 不同（逐个竖线换行 vs 挤在第二行），同一份代码在两个版本下会互相判为「格式不对」，
 * CI 的 format-check 与本地 `prettier --check` 就会打架。短语化能让它稳定在一行内。
 */
export type RedeemReason = 'callback' | 'no-pending' | 'expired' | 'mismatch' | 'exchange';

/** 一次兑换的结局：要么拿到会话，要么被拒且说明该不该清掉待完成登录 */
export interface RedeemOutcome {
  /** authorized = 已拿到会话；rejected = 未通过校验或兑换失败 */
  kind: 'authorized' | 'rejected';
  /** 被拒原因；authorized 时为 null */
  reason: RedeemReason | null;
  /** 会话；未成功时为 null（避免调用方误用半成品对象） */
  session: OidcSession | null;
  /**
   * 调用方是否应当立即删除「待完成登录」。
   *
   * 语义刻意分开：`mismatch` / `no-pending` 属于「这次跳转不可信」，
   * 此时**不得写任何存储**（连删除都不做），否则伪造一次跳转就能把
   * 用户正在进行的正常登录清掉。
   */
  consumePending: boolean;
}

/** 兑换结果（`exchangeCode` 的返回） */
export type ExchangeResult = { kind: 'ok'; session: OidcSession } | { kind: 'failed' };

/** 兑换请求的入参 */
export interface ExchangeCodeOptions {
  /** 身份服务地址（允许带结尾斜杠） */
  issuer: string;
  /** 公开客户端标识 */
  clientId: string;
  /** 回跳带来的授权码 */
  code: string;
  /** 本地留存的随机串 */
  codeVerifier: string;
  /** 与授权时完全一致的回跳地址 */
  redirectUri: string;
  /** 可注入的 fetch（门禁脚本用假实现统计调用与检查请求） */
  fetchImpl?: typeof fetch;
  /** 可注入的当前时刻（默认取系统时间） */
  now?: number;
}

/** 完整流程的入参 */
export interface CompleteAuthorizationOptions {
  /** 地址栏 query（含或不含问号均可） */
  search: string;
  /** 从存储里读出的待完成登录；不存在传 null */
  pending: PendingAuthorization | null;
  /** 身份服务地址 */
  issuer: string;
  /** 公开客户端标识 */
  clientId: string;
  /** 可注入的 fetch */
  fetchImpl?: typeof fetch;
  /** 可注入的当前时刻 */
  now?: number;
  /**
   * 校验通过、**即将兑换之前**的回调（调用方在此删除「待完成登录」）。
   *
   * 为什么要在兑换之前：授权码是一次性的，本地状态更该是一次性的——先作废再兑换，
   * 才不会出现「兑换挂在网络上、状态还留着」的那段可被并发重放的窗口。
   * 纯函数层不认识存储，因此把这个副作用以回调形式注入，而不是把存储塞进本文件。
   */
  onValidated?: () => void;
}

/**
 * 去掉地址末尾的斜杠，避免拼出 `//protocol/...`。
 *
 * @param value - 原始地址
 * @returns 去掉结尾斜杠的地址
 */
function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, '');
}

/**
 * 取安全随机源。
 *
 * @returns 加密安全随机数发生器
 * @throws Error 环境不支持时抛出业务语言异常
 */
function secureRandom(): Crypto {
  const source = typeof globalThis !== 'undefined' ? globalThis.crypto : undefined;
  if (!source || typeof source.getRandomValues !== 'function') {
    throw new Error(UNSUPPORTED_ENVIRONMENT_MESSAGE);
  }
  return source;
}

/**
 * 字节数组 → base64 文本。
 *
 * 不依赖 Buffer（浏览器不存在），只用标准的 `btoa`。
 *
 * @param bytes - 字节数组
 * @returns base64 文本
 */
function encodeBase64(bytes: Uint8Array): string {
  let binary = '';
  for (let index = 0; index < bytes.length; index += 1) {
    binary += String.fromCharCode(bytes[index] ?? 0);
  }
  if (typeof btoa !== 'function') {
    throw new Error(UNSUPPORTED_ENVIRONMENT_MESSAGE);
  }
  return btoa(binary);
}

/**
 * 字节数组 → base64url 文本（`-` `_` 替换、去掉尾部 `=`）。
 *
 * 授权地址与挑战值都必须是 URL 安全字符集，否则会被二次编码导致校验失败。
 *
 * @param bytes - 字节数组
 * @returns base64url 文本
 */
export function base64UrlEncode(bytes: Uint8Array): string {
  return encodeBase64(bytes).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/**
 * 生成指定字节数的随机 base64url 串。
 *
 * @param byteLength - 字节数，默认 32（→ 43 字符）
 * @returns base64url 随机串
 */
export function randomBase64Url(byteLength: number = RANDOM_BYTE_LENGTH): string {
  const bytes = new Uint8Array(byteLength);
  secureRandom().getRandomValues(bytes);
  return base64UrlEncode(bytes);
}

/**
 * 生成 PKCE 对（S256）。
 *
 * 摘要算法用 `crypto.subtle`：非安全上下文（http 且非本机）下它是 undefined，
 * 此时**必须失败**而不是退化成 `plain`——退化成 plain 等于没有 PKCE，
 * 一旦回跳参数被截获就能直接兑换。
 *
 * @returns PKCE 对（verifier 43 字符、challenge 43 字符、方法 S256）
 */
export async function createPkcePair(): Promise<PkcePair> {
  const verifier = randomBase64Url(RANDOM_BYTE_LENGTH);
  const subtle = secureRandom().subtle;
  if (!subtle || typeof subtle.digest !== 'function') {
    throw new Error(UNSUPPORTED_ENVIRONMENT_MESSAGE);
  }
  const digest = await subtle.digest('SHA-256', new TextEncoder().encode(verifier));
  return {
    codeVerifier: verifier,
    codeChallenge: base64UrlEncode(new Uint8Array(digest)),
    codeChallengeMethod: 'S256',
  };
}

/**
 * 生成一次性 state。
 *
 * @returns 32 字节随机 base64url 串（43 字符）
 */
export function createState(): string {
  return randomBase64Url(RANDOM_BYTE_LENGTH);
}

/**
 * 拼装授权页地址。
 *
 * 参数顺序固定（便于门禁逐字断言，也便于排障时肉眼比对）。
 *
 * @param options - 拼装参数
 * @returns 完整的授权页地址
 */
export function buildAuthorizeUrl(options: AuthorizeUrlOptions): string {
  const query = new URLSearchParams({
    client_id: options.clientId,
    redirect_uri: options.redirectUri,
    response_type: 'code',
    scope: options.scope,
    state: options.state,
    code_challenge: options.codeChallenge,
    code_challenge_method: 'S256',
  });
  return `${trimTrailingSlash(options.issuer)}/protocol/openid-connect/auth?${query.toString()}`;
}

/**
 * 解析回跳参数。
 *
 * 判定顺序刻意如此（先看服务端拒绝，再看缺失，最后才看正常）：
 * 服务端明确拒绝时，即使地址里同时带着半截参数也不能当成「正常」。
 *
 * @param search - 地址栏 query（含或不含问号均可）
 * @returns 判别联合：ok / error / missing
 */
export function parseCallbackQuery(search: string): CallbackParseResult {
  const raw = typeof search === 'string' ? search : '';
  const params = new URLSearchParams(raw.startsWith('?') ? raw.slice(1) : raw);

  if ((params.get('error') ?? '').trim() !== '') {
    return { kind: 'error', reason: 'provider-error' };
  }

  const code = (params.get('code') ?? '').trim();
  const state = (params.get('state') ?? '').trim();

  // 两个都没有：用户是直接打开本页的，不是从身份服务回来的
  if (code === '' && state === '') {
    return { kind: 'missing' };
  }
  if (state === '') {
    return { kind: 'error', reason: 'missing-state' };
  }
  if (code === '') {
    return { kind: 'error', reason: 'missing-code' };
  }

  return { kind: 'ok', code, state };
}

/**
 * 判断「待完成登录」是否已过期。
 *
 * 三种异常都按过期处理（宁可让用户重来，也不接受不可信的时间戳）：
 * - 时间戳非法（NaN / 0 / 负数）；
 * - 创建时间比当前时间还晚超过容差（本机时钟被回拨）；
 * - 距创建已超过 TTL（恰好等于 TTL 也算过期）。
 *
 * @param pending - 待完成登录
 * @param now - 当前时刻（毫秒），默认取系统时间
 * @returns 过期返回 true
 */
export function isPendingExpired(pending: PendingAuthorization, now: number = Date.now()): boolean {
  const createdAt = pending?.createdAt;
  if (typeof createdAt !== 'number' || !Number.isFinite(createdAt) || createdAt <= 0) {
    return true;
  }
  const age = now - createdAt;
  if (age < -CLOCK_SKEW_MS) return true;
  return age >= PENDING_TTL_MS;
}

/**
 * 归一化身份服务下发的有效期。
 *
 * @param raw - 原始值（可能是数字或字符串）
 * @returns 有效期秒数（落在 1 ~ MAX_LIFETIME_SECONDS 之间）
 */
function normalizeLifetimeSeconds(raw: unknown): number {
  const value =
    typeof raw === 'number' ? raw : typeof raw === 'string' ? Number.parseFloat(raw) : Number.NaN;
  if (!Number.isFinite(value) || value <= 0) return DEFAULT_LIFETIME_SECONDS;
  return Math.min(value, MAX_LIFETIME_SECONDS);
}

/**
 * 用授权码兑换会话（公开客户端，**不带客户端密钥**）。
 *
 * 任何异常都收敛为 `{kind:'failed'}`：本函数不抛业务异常，
 * 因为调用方只需要「成功 / 未成功」两态，不需要（也不应展示）底层细节。
 *
 * @param options - 兑换参数
 * @returns 兑换结果
 */
export async function exchangeCode(options: ExchangeCodeOptions): Promise<ExchangeResult> {
  const doFetch = options.fetchImpl ?? (typeof fetch === 'function' ? fetch : null);
  if (!doFetch) return { kind: 'failed' };

  const endpoint = `${trimTrailingSlash(options.issuer)}/protocol/openid-connect/token`;
  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    client_id: options.clientId,
    code: options.code,
    redirect_uri: options.redirectUri,
    code_verifier: options.codeVerifier,
  });

  let response: Response;
  try {
    response = await doFetch(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        Accept: 'application/json',
      },
      body: body.toString(),
      // 不带任何浏览器凭据：本请求只靠 PKCE 证明「是同一个人发的」
      credentials: 'omit',
      cache: 'no-store',
    });
  } catch {
    return { kind: 'failed' };
  }

  if (!response || response.ok !== true) return { kind: 'failed' };

  let payload: Record<string, unknown>;
  try {
    payload = (await response.json()) as Record<string, unknown>;
  } catch {
    return { kind: 'failed' };
  }

  const accessToken = typeof payload.access_token === 'string' ? payload.access_token.trim() : '';
  if (accessToken === '') return { kind: 'failed' };

  const refreshToken =
    typeof payload.refresh_token === 'string' && payload.refresh_token.trim() !== ''
      ? payload.refresh_token.trim()
      : null;

  const now = options.now ?? Date.now();
  const lifetimeMs = normalizeLifetimeSeconds(payload.expires_in) * 1000;
  // 至少留 1 秒，避免「刚写进去就已经过期」的抖动
  const expiresAt = Math.max(now + 1000, now + lifetimeMs - CLOCK_SKEW_MS);

  return {
    kind: 'ok',
    session: { access_token: accessToken, refresh_token: refreshToken, expires_at: expiresAt },
  };
}

/**
 * 完整的一次回跳处理：解析 → 校验 → 兑换。
 *
 * 顺序即安全约束：**任何校验未通过都不发起兑换**（门禁用假 fetch 的调用计数断言）。
 *
 * @param options - 处理参数
 * @returns 兑换结局（含「是否该清掉待完成登录」的指示）
 */
export async function completeAuthorization(
  options: CompleteAuthorizationOptions,
): Promise<RedeemOutcome> {
  const now = options.now ?? Date.now();
  const parsed = parseCallbackQuery(options.search);

  if (parsed.kind !== 'ok') {
    return { kind: 'rejected', reason: 'callback', session: null, consumePending: false };
  }

  const pending = options.pending;
  if (!pending) {
    return { kind: 'rejected', reason: 'no-pending', session: null, consumePending: false };
  }
  if (isPendingExpired(pending, now)) {
    // 过期就清掉：留着只会让用户每次回来都撞同一个死状态
    return { kind: 'rejected', reason: 'expired', session: null, consumePending: true };
  }
  if (parsed.state !== pending.state) {
    // 不匹配时既不兑换、也不写存储：这可能是伪造跳转，不能让它可以清掉正常登录
    return { kind: 'rejected', reason: 'mismatch', session: null, consumePending: false };
  }

  // 校验全部通过：先作废本地状态（一次性），再兑换——顺序不可对调
  options.onValidated?.();

  const result = await exchangeCode({
    issuer: options.issuer,
    clientId: options.clientId,
    code: parsed.code,
    codeVerifier: pending.codeVerifier,
    redirectUri: pending.redirectUri,
    fetchImpl: options.fetchImpl,
    now,
  });

  if (result.kind !== 'ok') {
    // 授权码已被消耗，继续留着 pending 无意义
    return { kind: 'rejected', reason: 'exchange', session: null, consumePending: true };
  }

  return { kind: 'authorized', reason: null, session: result.session, consumePending: true };
}
