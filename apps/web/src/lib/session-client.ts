/**
 * 会话端点客户端 —— 浏览器与「统一登录」会话能力的**唯一**通道。
 *
 * 为什么要有这一层（P0-5 路线 A：令牌不进 JavaScript）
 * --------------------------------------------------
 * 改造前，访问令牌存在本地存储里，并在每个请求上注入 `Authorization: Bearer`。
 * 本地存储对同源脚本完全可读，因此任何一处 XSS 都能直接取走令牌；而浏览器
 * 唯一的令牌免疫区是 httpOnly Cookie —— 服务端下发、JS 既读不到也写不了。
 *
 * 因此本文件只做一件事：**用 Cookie 说话**。五个函数分别对应服务端五个端点，
 * 全部 `credentials: 'include'`（让浏览器带上 httpOnly Cookie）、`cache: 'no-store'`
 * （会话状态不可缓存，缓存一个「已登录」比不缓存危险得多）。
 *
 * 红线（改代码前先读完，这几条是本次改造的全部意义）
 * ------------------------------------------------
 * 1. **不碰任何 Cookie**：`oa_session` / `oa_refresh` 全是 httpOnly，通过 Cookie
 *    读写 API 既读不到（读了也是空串，反而容易被误判成「未登录」）也不该写；
 * 2. **不保存、不返回任何令牌**：五个端点的响应体里没有 `access_token` /
 *    `refresh_token` / `id_token`（服务端硬约束），本文件也不做任何本地持久化；
 * 3. **不记录日志**：会话相关的一切都不进控制台。
 *
 * 端点前缀不在 `/api/v1` 下：服务端把会话端点挂在 `/sso/`（见
 * `apps/adapter/app/api/sso_routes.py` 的模块说明），因此这里用 `/sso` 而不是
 * `api-client` 的 `API_PREFIX`。基址仍取同一个环境变量：默认空串 = 同源相对路径
 * （边缘化部署后浏览器不该直连适配层端口）。
 */

// ---------------------------------------------------------------------------
// 类型
// ---------------------------------------------------------------------------

/**
 * 会话状态四元组（与后端 `SsoSessionData` 逐字段对齐）。
 *
 * **为什么不是一个 `authenticated` 布尔**（服务端 QA-01 的客户端版本）：
 * 只有一个布尔时，「访问令牌过期但刷新令牌还有效」与「从未登录」的响应体完全
 * 相同，前端只能一律跳登录页 —— 而 access TTL 是 300 秒、会话上限是 8 小时，
 * 于是 8 小时的会话被压成 5 分钟，用户每 5 分钟被踢一次。
 */
export interface SessionState {
  /** 访问令牌有效（Cookie 存在且验签通过） */
  authenticated: boolean;
  /** 访问令牌剩余有效期（秒）；未登录时 null */
  expires_in: number | null;
  /** 浏览器是否还持有刷新令牌（能否续期） */
  refreshable: boolean;
  /** 曾带着会话 Cookie 来访但它不可用（过期 / 签名不符 / 无法解析） */
  expired: boolean;
}

/** 完成登录 / 刷新会话的业务数据（**不含任何令牌**） */
export interface SessionGrant {
  /** 服务端是否完成了本次动作（能返回即成功） */
  ok: boolean;
  /** 访问令牌有效期（秒），供前端决定何时主动刷新 */
  expires_in: number;
}

/**
 * 一次从未登录的状态常量（用于兜底，不参与服务端判定）。
 *
 * 取值与「从未登录」的四元组逐字一致：`{false, null, false, false}`。
 */
export const ANONYMOUS_SESSION_STATE: SessionState = {
  authenticated: false,
  expires_in: null,
  refreshable: false,
  expired: false,
};

// ---------------------------------------------------------------------------
// 错误
// ---------------------------------------------------------------------------

/** 会话端点不可用（网络层）时的业务语言提示 */
const UNAVAILABLE_MESSAGE = '统一登录服务暂时不可用，请稍后再试';

/** 响应体无法解析时的业务语言提示 */
const MALFORMED_MESSAGE = '统一登录服务响应异常，请稍后再试';

/** 授权地址缺失时的业务语言提示 */
const MISSING_AUTHORIZE_URL_MESSAGE = '统一登录服务未返回有效的登录地址，请稍后再试';

/** 调用方漏传授权码 / 状态串时的业务语言提示（正常路径不可达） */
const MISSING_CODE_MESSAGE = '本次登录未完成，请重新登录';

/**
 * 会话请求失败。
 *
 * 必须同时携带 **HTTP 状态码**与**业务码**，因为「刷新失败该怎么处置」由两者
 * 共同决定（见 `auth-refresh.ts` 的四分类）：401 与 20002 都要跳登录页；
 * 429/20006 要退避重试；503/30001、504/30002 也要退避但绝不跳登录页。
 * 两条信息混成一个字符串就等于把判据丢给了正则。
 *
 * `status === 0` 表示**没有拿到响应**（断网、超时、DNS 失败）：
 * 与服务端明确拒绝是两件事，处置也不同（前者绝不能推出「登录失效」）。
 *
 * `retryable` 是服务端的**明确可重试信号**（`data.retryable = true`）：典型场景是
 * 并发刷新时「被新轮换取代的旧令牌」迟到被判 20002——此时会话其实健在，
 * 绝不能按「已死」处置（M1：否则多标签页用户被误踢下线）。
 */
export class SessionRequestError extends Error {
  /** HTTP 状态码；0 = 未拿到响应（网络层失败） */
  readonly status: number;
  /** 后端业务码；0 = 无信封 / 未解析出业务码 */
  readonly code: number;
  /** 服务端明确标注「可重试」（`data.retryable = true`）；缺失/false = 未标注 */
  readonly retryable: boolean;

  /**
   * @param message - 业务语言文案（可直接展示给用户）
   * @param status - HTTP 状态码（网络层失败传 0）
   * @param code - 后端业务码（无信封传 0）
   * @param retryable - 服务端是否明确标注可重试（默认 false）
   */
  constructor(message: string, status: number, code: number, retryable: boolean = false) {
    super(message);
    this.name = 'SessionRequestError';
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }
}

// ---------------------------------------------------------------------------
// 运行时配置
// ---------------------------------------------------------------------------

/**
 * 会话服务基址。
 *
 * 缺省 `''` = 同源相对路径：边缘化 / 反代部署后浏览器与适配层同源，直连
 * `http://127.0.0.1:8000` 既打不通（用户机器上没有这个端口）也会被浏览器的
 * 混合内容与 CORS 规则拒掉。
 */
const SSO_BASE_URL: string = process.env.NEXT_PUBLIC_ADAPTER_BASE_URL ?? '';

/** 会话端点前缀（**不在** `/api/v1` 下） */
const SSO_PREFIX = '/sso';

/** 会话请求超时（毫秒）。比业务请求短：登录态探针挂在首屏，不能拖住渲染 */
const SESSION_TIMEOUT_MS = 15_000;

// ---------------------------------------------------------------------------
// 底层请求
// ---------------------------------------------------------------------------

/** 统一响应信封 */
interface ApiEnvelope<T> {
  code: number;
  message: string;
  data: T | null;
}

/**
 * 发起一次会话端点请求并拆信封。
 *
 * @param path - 端点路径（不含 `/sso` 前缀）
 * @param init - fetch 参数（`credentials` / `cache` 由本函数强制补齐）
 * @param timeoutMs - 超时毫秒数
 * @returns `data` 字段
 * @throws SessionRequestError 网络失败、响应无法解析、业务码非 0
 */
async function callSession<T>(
  path: string,
  init: RequestInit,
  timeoutMs: number = SESSION_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let response: Response;
  try {
    response = await fetch(`${SSO_BASE_URL}${SSO_PREFIX}${path}`, {
      ...init,
      headers: { Accept: 'application/json', ...(init.headers ?? {}) },
      // 两个凭据相关选项由本函数统一补齐，调用方不得覆盖：
      // - include：浏览器带上 httpOnly Cookie（**令牌唯一的通道**）
      // - no-store：会话状态不可缓存（缓存一个「已登录」比不缓存危险得多）
      credentials: 'include',
      cache: 'no-store',
      signal: controller.signal,
    });
  } catch {
    // 断网 / 超时 / 被浏览器拦截：都没有拿到响应，status 记 0
    throw new SessionRequestError(UNAVAILABLE_MESSAGE, 0, 0);
  } finally {
    clearTimeout(timer);
  }

  let payload: ApiEnvelope<T> | null = null;
  try {
    payload = (await response.json()) as ApiEnvelope<T>;
  } catch {
    payload = null;
  }

  if (payload === null || typeof payload !== 'object' || typeof payload.code !== 'number') {
    // 没有信封（网关 5xx 的 HTML 错误页、空响应体）：把 HTTP 状态原样带出去，
    // 让上层的四分类仍能按 5xx 判定「临时故障，不跳登录页」。
    throw new SessionRequestError(MALFORMED_MESSAGE, response.status, 0);
  }

  if (payload.code !== 0) {
    const message =
      typeof payload.message === 'string' && payload.message.trim() !== ''
        ? payload.message
        : UNAVAILABLE_MESSAGE;
    // 服务端的明确可重试信号（M1）：`data.retryable = true` 表示「这次失败不是
    // 会话失效」（如被新轮换取代的旧令牌迟到），四分类据此归「可重试」。
    const retryable =
      typeof payload.data === 'object' &&
      payload.data !== null &&
      (payload.data as { retryable?: unknown }).retryable === true;
    throw new SessionRequestError(message, response.status, payload.code, retryable);
  }

  return payload.data as T;
}

/**
 * 把服务端返回的会话数据归一化成四元组。
 *
 * 服务端契约是四个字段，但传输层的值**不能直接当类型用**：字段缺失、`expires_in`
 * 是字符串或负数、布尔是 `0/1` 都出现过。统一在这里做一次归一化，下游
 * （`auth-refresh` 三态判据）就永远面对干净的取值。
 *
 * @param raw - 服务端 `data`（可能是 null 或缺字段）
 * @returns 归一化后的会话状态
 */
export function normalizeSessionState(raw: unknown): SessionState {
  if (typeof raw !== 'object' || raw === null) {
    return { ...ANONYMOUS_SESSION_STATE };
  }
  const source = raw as Record<string, unknown>;
  const expiresRaw = source.expires_in;
  const expiresIn =
    typeof expiresRaw === 'number' && Number.isFinite(expiresRaw) && expiresRaw >= 0
      ? Math.floor(expiresRaw)
      : null;
  return {
    authenticated: source.authenticated === true,
    expires_in: expiresIn,
    refreshable: source.refreshable === true,
    expired: source.expired === true,
  };
}

// ---------------------------------------------------------------------------
// 五个端点
// ---------------------------------------------------------------------------

/**
 * 发起统一登录（`POST /sso/start`）。
 *
 * 服务端在这里生成 PKCE 挑战并把 verifier 存进 httpOnly 的暂存 Cookie，
 * 前端只负责拿到授权地址并跳转 —— **verifier 不再经过 JavaScript**，
 * 这是本轮改造最直接的收益（改造前它存在可被脚本读取的会话级存储里）。
 *
 * @param next - 登录后要回到的站内路径（可选；调用方须先经 `resolveNextPath` 校验）
 * @returns 应跳转的授权地址
 * @throws SessionRequestError 端点不可用（含 `auth_mode=stub` 的 30001）、响应缺地址
 */
export async function startLogin(next: string = ''): Promise<string> {
  const trimmed = next.trim();
  const data = await callSession<{ authorize_url?: string }>('/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(trimmed === '' ? {} : { next: trimmed }),
  });
  const authorizeUrl = typeof data?.authorize_url === 'string' ? data.authorize_url.trim() : '';
  if (authorizeUrl === '') {
    throw new SessionRequestError(MISSING_AUTHORIZE_URL_MESSAGE, 0, 0);
  }
  return authorizeUrl;
}

/**
 * 完成统一登录（`POST /sso/complete`）。
 *
 * 服务端校验暂存 Cookie 里的 `state`，用授权码换令牌，然后**以 Set-Cookie 下发**；
 * 响应体只有 `ok` 与 `expires_in`。成功后浏览器已经持有 httpOnly 会话 Cookie，
 * 前端不需要（也无法）保存任何东西。
 *
 * @param code - 回调带来的授权码
 * @param state - 回调带来的状态串（服务端与暂存 Cookie 逐字比对）
 * @returns `{ok, expires_in}`
 * @throws SessionRequestError 校验失败（20002）、端点不可用（30001）、网络失败
 */
export async function completeLogin(code: string, state: string): Promise<SessionGrant> {
  const trimmedCode = typeof code === 'string' ? code.trim() : '';
  if (trimmedCode === '') {
    // 正常路径不可达（调用方已先解析回调参数），兜底避免发出注定被拒的请求
    throw new SessionRequestError(MISSING_CODE_MESSAGE, 0, 0);
  }
  const data = await callSession<{ ok?: boolean; expires_in?: number }>('/complete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code: trimmedCode, state: typeof state === 'string' ? state : '' }),
  });
  return readGrant(data);
}

/**
 * 查询会话状态（`GET /sso/session`）—— 启动时的**唯一**判据。
 *
 * 注意它**只读不写**：服务端在令牌不可用时会清掉 `oa_session`，但**绝不清**
 * `oa_refresh`（QA-01）——「access 过期、refresh 有效」是会话期内的正常状态。
 *
 * @returns 四元组会话状态（绝不包含令牌）
 * @throws SessionRequestError 端点不可用（30001）、网络失败
 */
export async function fetchSession(): Promise<SessionState> {
  const data = await callSession<unknown>('/session', { method: 'GET' });
  return normalizeSessionState(data);
}

/**
 * 刷新会话（`POST /sso/refresh`）。
 *
 * 刷新令牌是**轮换**的：成功后旧值立刻失效，因此并发刷新会互相打死（后到的那次
 * 拿着已作废的令牌），必须由 `auth-refresh.refreshWithRetry()` 串行化 +
 * 退避 + 抖动，调用方不要直接并发调用本函数。
 *
 * @returns `{ok, expires_in}`（新的有效期，用于重排下次刷新）
 * @throws SessionRequestError 刷新令牌已死（401 / 20002）、限流（429 / 20006）、
 *   端点不可用（503 / 30001、504 / 30002）、网络失败
 */
export async function refreshSession(): Promise<SessionGrant> {
  const data = await callSession<{ ok?: boolean; expires_in?: number }>('/refresh', {
    method: 'POST',
  });
  return readGrant(data);
}

/**
 * 退出登录（`POST /sso/logout`）。
 *
 * 服务端删两个会话 Cookie 并尽力撤销刷新令牌。撤销失败不影响本地结果
 * （Cookie 已删，浏览器不再持有任何令牌），因此本函数只关心请求是否送达。
 *
 * @throws SessionRequestError 端点不可用（30001）、网络失败
 */
export async function logoutSession(): Promise<void> {
  await callSession<{ ok?: boolean }>('/logout', { method: 'POST' });
}

/**
 * 归一化 `{ok, expires_in}`。
 *
 * @param data - 服务端 `data`
 * @returns 归一化后的结果（`expires_in` 非法时归 0，由调用方决定是否重排刷新）
 */
function readGrant(data: { ok?: boolean; expires_in?: number } | null): SessionGrant {
  const raw = data?.expires_in;
  const expiresIn =
    typeof raw === 'number' && Number.isFinite(raw) && raw > 0 ? Math.floor(raw) : 0;
  return { ok: data?.ok !== false, expires_in: expiresIn };
}
