/**
 * 会话刷新策略 —— 三态判据、四分类处置、退避与抖动。
 *
 * 这个文件的存在理由，是把服务端刚刚修掉的三类缺陷**在客户端侧对称地修掉**：
 *
 * | 服务端缺陷 | 客户端等价物 | 本文件的对策 |
 * | --- | --- | --- |
 * | QA-01 探针顺手删了刷新令牌 → 8h 会话压成 300s | 用 `authenticated` 单布尔判「没登录」→ 每 5 分钟踢一次 | {@link decideSession} 三态判据 |
 * | QA-02 5xx 被误判为凭证失效 → 一次抖动强制重新登录 | `/sso/refresh` 失败一律跳登录页 | {@link refreshWithRetry} 四分类 |
 * | QA-07 刷新风暴（多标签页同时刷新） | 提前量固定、无抖动 → 所有标签页同一毫秒发起 | {@link nextRefreshDelay} 加 0–30s 抖动 |
 *
 * 设计口径（刻意如此）：
 * - **纯函数优先**：`decideSession` / `nextRefreshDelay` / `classifyRefreshFailure`
 *   全部是输入即输出的纯函数，可以用普通断言穷举矩阵；
 * - **副作用可注入**：`refresh` / `sleep` / `random` / `setTimer` / `clearTimer`
 *   都可注入，因此时序类断言用 `vi.useFakeTimers()` 就能精确复现，不需要真等待；
 * - **绝不猜测**：判据只认服务端给的四个字段与 HTTP 状态码/业务码，不做
 *   「大概是过期了吧」这类推断 —— 推断错一次就是用户被无故登出。
 */

import {
  refreshSession,
  SessionRequestError,
  type SessionGrant,
  type SessionState,
} from './session-client';

// ---------------------------------------------------------------------------
// 常量（导出便于单测与调用方对齐，不要在各处再写一遍数字）
// ---------------------------------------------------------------------------

/** 主动刷新的**最小**提前量（毫秒）：至少提前 30 秒，避免踩在过期边界上刷新 */
export const LEAD_TIME_MIN_MS = 30_000;

/** 主动刷新的**最大**额外提前量（毫秒）：提前量在 30–60 秒之间随机 */
export const LEAD_TIME_SPREAD_MS = 30_000;

/** 抖动上限（毫秒）：叠加 0–30 秒随机抖动，打散多标签页的同步刷新 */
export const JITTER_MAX_MS = 30_000;

/** 刷新重试的最多次数（不含首次）：合计最多 3 次请求 */
export const MAX_REFRESH_RETRIES = 2;

/** 重试退避基数（毫秒）：1s → 2s */
export const RETRY_BASE_DELAY_MS = 1_000;

/** 每次重试附加的抖动上限（毫秒） */
export const RETRY_JITTER_MAX_MS = 300;

/** 刷新定时器的最短间隔（毫秒）：有效期极短时也不能打成紧循环 */
export const MIN_REFRESH_DELAY_MS = 1_000;

/** 有效期未知时的保守刷新间隔（毫秒）：与服务端 access TTL 300s 同量级 */
export const DEFAULT_REFRESH_DELAY_MS = 240_000;

/**
 * 临时故障后重排下一轮的间隔（毫秒）。
 *
 * 退避重试次数用尽后仍失败，说明对端在较长时间内不可用（网关维护、身份平台
 * 重启）。此时既不能跳登录页（用户没做错什么），也不能 1 秒一次地打（那就是
 * 自造风暴）。
 */
export const RETRY_AFTER_FAILURE_MS = 60_000;

/** 服务端 `auth_mode=stub` 时的固定文案：出现它说明整条会话链路未启用 */
const SSO_DISABLED_HINT = '统一登录未启用';

// ---------------------------------------------------------------------------
// 三态判据
// ---------------------------------------------------------------------------

/** 会话处置三态：直接用 / 去刷新 / 去登录页 */
export type SessionDecision = 'use' | 'refresh' | 'login';

/**
 * 按四元组决定该做什么（**不要**用 `authenticated` 单布尔判断）。
 *
 * 四组取值与动作的对应关系（这张表就是实现契约，改判据先改这里）：
 *
 * | 四元组 | 含义 | 动作 |
 * | --- | --- | --- |
 * | `{T, N>0, *, F}` | 有效 | `use` |
 * | `{F, null, T, T}` | 过期但可刷新 | `refresh` |
 * | `{F, null, F, T}` | 过期且刷新也没了 | `login` |
 * | `{F, null, F, F}` | 从未登录 | `login` |
 *
 * 判定顺序是**先可信后可救**：`authenticated && !expired` 一定直接用（哪怕
 * `expires_in` 缺失 —— 有效期只影响「何时刷新」，不影响「能不能用」）；其余情况
 * 只要还能刷新就先刷新（刷新失败再按四分类处置），最后才跳登录页。
 *
 * @param state - 会话状态四元组
 * @returns 处置三态
 */
export function decideSession(state: SessionState): SessionDecision {
  if (state.authenticated && !state.expired) {
    return 'use';
  }
  if (state.refreshable) {
    return 'refresh';
  }
  return 'login';
}

/**
 * 是否应当调用 `/sso/refresh`。
 *
 * @param state - 会话状态四元组
 * @returns 需要刷新返回 true
 */
export function shouldRefresh(state: SessionState): boolean {
  return decideSession(state) === 'refresh';
}

// ---------------------------------------------------------------------------
// 四分类
// ---------------------------------------------------------------------------

/** 一次刷新的失败分类（决定了「要不要跳登录页」） */
export type RefreshFailureKind = 'dead' | 'retry';

/**
 * 「刷新令牌已死」的两个客观信号。
 *
 * 一个是传输层状态（网关直出的 401），一个是业务码（信封里的 20002）。
 * 两者必须都在：网关层的 401 往往没有统一信封，只有状态码可看；而经适配层
 * 转发的失败一定带业务码。
 */
const DEAD_HTTP_STATUS = 401;
const DEAD_BUSINESS_CODE = 20002;

/**
 * 「临时故障」的客观信号（限流与网关/上游不可用）。
 *
 * 取值来自 QA 对 `/sso/refresh` 的逐个实测，**不要合并成 `status >= 500`**：
 * 那样 429（限流）会掉进「未知」分支，而 429 恰恰是最该退避重试的一类。
 */
const RATE_LIMITED_HTTP_STATUS = 429;
const RATE_LIMITED_BUSINESS_CODE = 20006;
const UNAVAILABLE_HTTP_STATUS = 503;
const UNAVAILABLE_BUSINESS_CODE = 30001;
const GATEWAY_TIMEOUT_HTTP_STATUS = 504;
const GATEWAY_TIMEOUT_BUSINESS_CODE = 30002;

/**
 * 判断是否是「刷新令牌已死」。
 *
 * @param error - 会话请求失败
 * @returns 已死返回 true（此时服务端已清 `oa_refresh`，重试没有任何意义）
 */
function isDeadSignal(error: SessionRequestError): boolean {
  return error.status === DEAD_HTTP_STATUS || error.code === DEAD_BUSINESS_CODE;
}

/**
 * 判断是否是「临时故障」。
 *
 * @param error - 会话请求失败
 * @returns 临时故障返回 true（该退避重试，绝不能跳登录页）
 */
function isTransientSignal(error: SessionRequestError): boolean {
  return (
    error.status === RATE_LIMITED_HTTP_STATUS ||
    error.code === RATE_LIMITED_BUSINESS_CODE ||
    error.status === UNAVAILABLE_HTTP_STATUS ||
    error.code === UNAVAILABLE_BUSINESS_CODE ||
    error.status === GATEWAY_TIMEOUT_HTTP_STATUS ||
    error.code === GATEWAY_TIMEOUT_BUSINESS_CODE
  );
}

/**
 * 把刷新失败分成「死透」与「可重试」两类（四分类的第二层）。
 *
 * 判据只用两个客观事实：HTTP 状态码与后端业务码。
 *
 * | 返回 | 分类 | 理由 |
 * | --- | --- | --- |
 * | `401` / `20002` | `dead` | 刷新令牌已死（服务端同时已清 `oa_refresh`），重试多少次都不会成功 |
 * | `429` / `20006` | `retry` | 限流：等一会儿再来，**绝不能**当成登录失效 |
 * | `503` / `30001`、`504` / `30002` | `retry` | 网关/上游不可用：临时故障 |
 * | 其他 5xx | `retry` | 同上 |
 * | 其他 4xx（未知） | `retry` | 语义不明时**不登出**：一次误判就是把用户踢下线 |
 * | `status === 0`（无响应） | `retry` | 断网/超时与「令牌失效」是两件事 |
 *
 * @param error - 刷新抛出的异常
 * @returns `dead` = 跳登录页；`retry` = 退避重试且**绝不**跳登录页
 */
export function classifyRefreshFailure(error: unknown): RefreshFailureKind {
  if (!(error instanceof SessionRequestError)) {
    // 非预期异常（编程错误、第三方库抛错）：按临时故障处理，不登出用户
    return 'retry';
  }
  if (isDeadSignal(error)) {
    return 'dead';
  }
  // 显式枚举「临时故障」的两个来源（限流 / 网关与上游不可用）：这段分支不做处置，
  // 但它是契约的一部分——把 429 与 5xx 从「死透」里分出来，正是服务端 QA-02 的订正。
  if (isTransientSignal(error)) {
    return 'retry';
  }
  // 其余（未知 4xx、无响应、未枚举的 5xx）同样不登出：语义不明时保持会话
  return 'retry';
}

/**
 * 判断失败是否是「统一登录整体未启用」（`auth_mode=stub`）。
 *
 * 为什么要单独辨认：`auth_mode=stub` 下五个端点**永远**返回 503/30001。
 * 对 30001 一味退避重试会在联调环境打成死循环（每秒一次、永远不成功），
 * 因此在文案里认出这种「配置决定的不可能」后直接放弃，不再重试。
 *
 * @param error - 刷新抛出的异常
 * @returns 命中「统一登录未启用」返回 true
 */
export function isSsoDisabled(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false;
  }
  return error.message.includes(SSO_DISABLED_HINT);
}

/**
 * 计算第 `attempt` 次重试前的等待毫秒数（指数退避 + 抖动）。
 *
 * 退避序列（`baseDelayMs = 1000`）：`1s + 0..300ms` → `2s + 0..300ms`。
 * 抖动的作用不是好看：多副本/多标签页同时撞上 429 时，没有抖动就会在
 * 同一毫秒再次形成一波，把限流变成永久限流。
 *
 * @param attempt - 第几次重试（从 0 开始）
 * @param baseDelayMs - 退避基数（毫秒）
 * @param jitterMaxMs - 抖动上限（毫秒）
 * @param random - 随机源（默认 `Math.random`，单测注入）
 * @returns 等待毫秒数（不小于 0）
 */
export function retryDelay(
  attempt: number,
  baseDelayMs: number = RETRY_BASE_DELAY_MS,
  jitterMaxMs: number = RETRY_JITTER_MAX_MS,
  random: () => number = Math.random,
): number {
  const safeAttempt = Number.isFinite(attempt) && attempt > 0 ? Math.floor(attempt) : 0;
  const backoff = baseDelayMs * 2 ** safeAttempt;
  const jitter = Math.floor(clamp01(random()) * Math.max(0, jitterMaxMs));
  return Math.max(0, backoff + jitter);
}

/**
 * 把随机源取值限制在 `[0, 1)`。
 *
 * 注入的随机源可能是残缺实现（返回 NaN / 负数 / 1 以上），直接用会把定时器
 * 算成负数或翻倍；这里兜住，避免一处注入错误变成「刷新时间不可预测」。
 *
 * @param value - 随机源输出
 * @returns `[0, 1)` 内的值
 */
function clamp01(value: number): number {
  if (!Number.isFinite(value) || value <= 0) {
    return 0;
  }
  if (value >= 1) {
    return 0.999_999;
  }
  return value;
}

// ---------------------------------------------------------------------------
// 刷新（含重试）
// ---------------------------------------------------------------------------

/** 刷新结局 */
export type RefreshOutcomeKind =
  /** 刷新成功：拿到新的有效期 */
  | 'refreshed'
  /** 刷新令牌已死：只能重新登录 */
  | 'relogin'
  /** 临时故障且重试已用尽：**不跳登录页**，等下一轮 */
  | 'transient';

/** 一次刷新的结局（含实际发出的请求次数，便于断言与排障） */
export interface RefreshOutcome {
  /** 结局 */
  kind: RefreshOutcomeKind;
  /** 成功时的新的有效期（秒）；失败时为 null */
  expires_in: number | null;
  /** 实际发出（或命中）的请求次数（含首次） */
  attempts: number;
  /** 最后一次失败的原因（成功时为 null；仅供排障，不展示给用户） */
  error: unknown;
}

/** `refreshWithRetry` 的可注入选项 */
export interface RefreshRetryOptions {
  /** 发起一次刷新（默认走 `session-client.refreshSession`） */
  refresh?: () => Promise<SessionGrant>;
  /** 重试次数上限（不含首次），默认 {@link MAX_REFRESH_RETRIES} */
  maxRetries?: number;
  /** 退避基数（毫秒），默认 {@link RETRY_BASE_DELAY_MS} */
  baseDelayMs?: number;
  /** 退避抖动上限（毫秒），默认 {@link RETRY_JITTER_MAX_MS} */
  jitterMaxMs?: number;
  /** 随机源（默认 `Math.random`） */
  random?: () => number;
  /** 等待实现（默认真实 `setTimeout`；单测注入假等待避免真等待） */
  sleep?: (ms: number) => Promise<void>;
}

/** 默认等待实现 */
function defaultSleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, Math.max(0, ms));
  });
}

/**
 * 刷新会话，按四分类处置，带指数退避 + 限次 + 抖动。
 *
 * **绝不抛异常**：所有结局都收敛成 {@link RefreshOutcome}。理由是调用点分布在
 * 传输层（401 拦截）与布局层（主动刷新）两处，任何一处漏 catch 都会把「刷新
 * 失败」升级成页面崩溃，而那时用户看到的是一张白屏而不是「重新登录」。
 *
 * @param options - 可注入项（生产路径全部走默认值）
 * @returns 刷新结局
 */
export async function refreshWithRetry(options: RefreshRetryOptions = {}): Promise<RefreshOutcome> {
  const refresh = options.refresh ?? refreshSession;
  const maxRetries = Math.max(0, Math.floor(options.maxRetries ?? MAX_REFRESH_RETRIES));
  const baseDelayMs = Math.max(0, options.baseDelayMs ?? RETRY_BASE_DELAY_MS);
  const jitterMaxMs = Math.max(0, options.jitterMaxMs ?? RETRY_JITTER_MAX_MS);
  const random = options.random ?? Math.random;
  const sleep = options.sleep ?? defaultSleep;

  let attempts = 0;
  let lastError: unknown = null;

  for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
    attempts += 1;
    try {
      const grant = await refresh();
      return { kind: 'refreshed', expires_in: grant.expires_in, attempts, error: null };
    } catch (error) {
      lastError = error;
      if (classifyRefreshFailure(error) === 'dead') {
        // 刷新令牌已死：服务端已清 Cookie，再重试只是白打身份平台
        return { kind: 'relogin', expires_in: null, attempts, error };
      }
      if (isSsoDisabled(error)) {
        // `auth_mode=stub`：五个端点永远 503。重试到底也只有一个结果，
        // 继续重试就是在联调环境里制造死循环
        return { kind: 'transient', expires_in: null, attempts, error };
      }
      if (attempt === maxRetries) {
        break;
      }
      await sleep(retryDelay(attempt, baseDelayMs, jitterMaxMs, random));
    }
  }

  return { kind: 'transient', expires_in: null, attempts, error: lastError };
}

// ---------------------------------------------------------------------------
// 主动刷新排期（含抖动）
// ---------------------------------------------------------------------------

/** `nextRefreshDelay` 的可注入选项 */
export interface RefreshDelayOptions {
  /** 随机源（默认 `Math.random`） */
  random?: () => number;
}

/**
 * 计算距离下一次主动刷新还有多久（毫秒）。
 *
 * 口径：**提前量 30–60 秒 + 0–30 秒抖动**，即总提前量落在 30–90 秒之间。
 *
 * 为什么提前量本身也是随机的：固定「提前 30 秒」时，同一批在同一毫秒登录的
 * 标签页会在同一毫秒一起刷新；服务端刷新令牌是**轮换**的，先到的那次让后到的
 * 那次拿着已作废的令牌 → 401 → 跳登录页。抖动把「同时」打散成 0–30 秒的分布，
 * 这才是刷新风暴的解法（服务端 QA-07 的客户端一半）。
 *
 * `expires_in` 为 `null`（未知）时给保守默认值；有效期极短时不低于
 * {@link MIN_REFRESH_DELAY_MS}，避免把定时器算成 0 而打成紧循环。
 *
 * @param expiresIn - 剩余有效期（秒）；未知传 null
 * @param options - 可注入项
 * @returns 下次刷新前的毫秒数
 */
export function nextRefreshDelay(
  expiresIn: number | null,
  options: RefreshDelayOptions = {},
): number {
  const random = options.random ?? Math.random;
  if (typeof expiresIn !== 'number' || !Number.isFinite(expiresIn)) {
    return DEFAULT_REFRESH_DELAY_MS;
  }
  const lifetimeMs = Math.max(0, expiresIn) * 1000;
  const leadMs = LEAD_TIME_MIN_MS + Math.floor(clamp01(random()) * LEAD_TIME_SPREAD_MS);
  const jitterMs = Math.floor(clamp01(random()) * JITTER_MAX_MS);
  const delay = lifetimeMs - leadMs - jitterMs;
  return Math.max(MIN_REFRESH_DELAY_MS, Math.floor(delay));
}

/** `scheduleRefresh` 的回调集合 */
export interface RefreshScheduleOptions {
  /** 发起一次刷新（默认 `refreshWithRetry()`） */
  refresh?: () => Promise<RefreshOutcome>;
  /** 刷新成功：带着新的有效期回调（调用方据此更新界面/日志） */
  onRefreshed?: (expiresIn: number | null) => void;
  /** 刷新令牌已死：调用方应跳登录页 */
  onRelogin?: () => void;
  /** 临时故障且重试用尽：调用方**不得**跳登录页，只提示「稍后再试」 */
  onUnavailable?: (outcome: RefreshOutcome) => void;
  /** 覆盖首轮等待毫秒数（默认由 {@link nextRefreshDelay} 计算） */
  delay?: number;
  /** 随机源（节流传给 {@link nextRefreshDelay}） */
  random?: () => number;
  /** 定时器实现（默认 `setTimeout`；单测注入以便精确驱动） */
  setTimer?: (handler: () => void, ms: number) => unknown;
  /** 清除定时器实现（默认 `clearTimeout`） */
  clearTimer?: (handle: unknown) => void;
}

/**
 * 排一次「到点主动刷新」，并在每次结束后自动重排（直到被取消）。
 *
 * 语义（三条，缺一条就会退化成风暴或永久失效）：
 * 1. **单轮串行**：上一轮刷新结束才排下一轮，绝不并发 → 轮换令牌不会被自己打作废；
 * 2. **成功按新有效期重排**（含抖动）→ 每个标签页的刷新时刻各自漂移；
 * 3. **临时故障按固定间隔重排**（{@link RETRY_AFTER_FAILURE_MS}）而不是放弃，
 *    同时**不跳登录页** —— 网关维护 10 分钟不该让全员重新登录。
 *
 * @param expiresIn - 当前访问令牌剩余有效期（秒）；未知传 null
 * @param options - 回调与可注入项
 * @returns 取消函数（组件卸载时必须调用；取消后不再触发任何回调）
 */
export function scheduleRefresh(
  expiresIn: number | null,
  options: RefreshScheduleOptions = {},
): () => void {
  const refresh = options.refresh ?? (() => refreshWithRetry({ random: options.random }));
  const setTimer =
    options.setTimer ?? ((handler: () => void, ms: number) => setTimeout(handler, ms) as unknown);
  const clearTimer =
    options.clearTimer ??
    ((handle: unknown) => clearTimeout(handle as ReturnType<typeof setTimeout>));

  let handle: unknown = null;
  let cancelled = false;

  const run = (delay: number): void => {
    handle = setTimer(
      () => {
        handle = null;
        void (async () => {
          const outcome = await refresh();
          if (cancelled) {
            // 卸载后不再触发任何回调，也不再排下一轮
            return;
          }
          if (outcome.kind === 'refreshed') {
            options.onRefreshed?.(outcome.expires_in);
            run(nextRefreshDelay(outcome.expires_in, { random: options.random }));
            return;
          }
          if (outcome.kind === 'relogin') {
            options.onRelogin?.();
            return;
          }
          options.onUnavailable?.(outcome);
          run(RETRY_AFTER_FAILURE_MS);
        })();
      },
      Math.max(0, delay),
    );
  };

  run(options.delay ?? nextRefreshDelay(expiresIn, { random: options.random }));

  return () => {
    cancelled = true;
    if (handle !== null) {
      clearTimer(handle);
      handle = null;
    }
  };
}
