#!/usr/bin/env node
/**
 * 登录闭环门禁（OIDC-PKCE）
 * ---------------------------------------------------------------------------
 * 依据：审计 A-01「登录闭环缺失：回跳页是占位、令牌只写不读」。这一条不是体验
 * 问题而是安全与可用性的双重缺口，因此做成静态 + 行为断言的门禁：
 *
 * 1. PKCE **必须** S256，且 `code_challenge === base64url(sha256(verifier))`
 *    （`plain` 等于没有 PKCE：回跳参数一旦被截获就能直接兑换）；
 * 2. 授权地址的 query 必须逐字包含 `response_type=code` / `code_challenge_method=S256`
 *    / 非空 `state` / 与发起时一致的 `redirect_uri`；
 * 3. 回跳参数解析必须覆盖四种输入（正常 / 服务端拒绝 / 缺授权码 / 缺一次性随机值）；
 * 4. **一次性随机值不匹配、或本地状态缺失/过期时，一次兑换请求都不许发**
 *    （用注入的假 fetch 统计调用次数）；校验通过时还必须**先作废本地状态再兑换**；
 * 5. 兑换请求必须是 `POST` + `application/x-www-form-urlencoded`，
 *    且 body 字段齐全、**绝不含 `client_secret`**（公开客户端不得持有密钥）；
 * 6. 兑换失败（断网 / 服务端拒绝）必须收敛为「未成功」，不得把异常抛给页面；
 * 7. 本地状态 10 分钟过期（11 分钟前创建必须判过期）；
 * 8. 会话存储：`expires_at: null`（无过期信息）**不判过期**、旧键
 *    `officeai_access_token` 能迁移到新键并删除旧键、存储不可用/内容损坏时
 *    降级为 null 而不是抛异常（否则一进页面就白屏或被莫名踢回登录页）。
 *
 * 用法（在 apps/web 下执行）：
 *   node scripts/check-oidc-auth.mjs                 # 全量断言
 *   node scripts/check-oidc-auth.mjs --json          # 输出 JSON（供 CI/报告消费）
 *   node scripts/check-oidc-auth.mjs --self-test     # 负样本自检（通过时返回 1）
 *
 * 退出码（与禁词 / 占位词 / 页面三态扫描器保持同一套约定，**故意如此**）：
 *   0 = 全部满足
 *   1 = 有断言不满足；`--self-test` 下 1 表示**自检通过**（故意写错的实现全被抓到）
 *   2 = 参数 / 文件错误（例如 `src/lib/oidc.ts` 不存在或无法装载）
 *
 * 设计要点：
 * - 断言跑的是**真实模块**（`import('../src/lib/oidc.ts')`，Node 22 原生类型剥离），
 *   不是复制一份实现来「自我印证」；
 * - 参考实现（sha256 → base64url）在本脚本内用 `node:crypto` 独立算一遍，
 *   避免「用被测代码验证被测代码」；
 * - `--self-test` 用若干「故意写错」的桩（明文挑战、漏 state、跳过校验、
 *   带密钥、永不过期、用 GET 兑换）证明门禁会红——门禁失效必须能被发现。
 */

import { createHash } from 'node:crypto';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = resolve(HERE, '..');
const OIDC_MODULE_PATH = resolve(WEB_ROOT, 'src/lib/oidc.ts');
const SESSION_STORE_PATH = resolve(WEB_ROOT, 'src/lib/session-store.ts');

/** 断言用的固定参数（不依赖真实环境，避免 CI 与本地差异） */
const ISSUER = 'https://sso.example.com/';
const CLIENT_ID = 'moy-web';
const REDIRECT_URI = 'https://app.example.com/auth/callback';
const SCOPE = 'openid email';
const NOW = Date.UTC(2026, 0, 1, 0, 0, 0);
const VERIFIER = 'A'.repeat(43);

/**
 * 独立计算 S256 挑战值（参考实现，与被测代码无关）。
 *
 * @param verifier - 随机串
 * @returns base64url(sha256(verifier))
 */
function referenceChallenge(verifier) {
  return createHash('sha256').update(verifier, 'ascii').digest('base64url');
}

/**
 * 从请求配置里读一个请求头（兼容对象字面量与 Headers 实例）。
 *
 * @param init - fetch 的第二参数
 * @param name - 头名
 * @returns 头值；没有返回空串
 */
function readHeader(init, name) {
  const headers = init?.headers;
  if (!headers) return '';
  if (typeof headers.get === 'function') return headers.get(name) ?? '';
  const key = Object.keys(headers).find((item) => item.toLowerCase() === name.toLowerCase());
  return key ? String(headers[key]) : '';
}

/**
 * 构造假的成功响应。
 *
 * @returns 最小可用的 Response 替身
 */
function okResponse() {
  return {
    ok: true,
    status: 200,
    json: async () => ({ access_token: 'AT-fake', refresh_token: 'RT-fake', expires_in: 300 }),
  };
}

// ---------------------------------------------------------------------------
// 逐条断言
// ---------------------------------------------------------------------------

/**
 * 断言 1：PKCE 必须 S256，挑战值 = base64url(sha256(verifier))，且每次都不一样。
 *
 * @param {object} impl - 被测实现
 * @returns {Promise<Array<string>>} 问题列表
 */
async function checkPkce(impl) {
  const problems = [];
  const pair = await impl.createPkcePair();
  const second = await impl.createPkcePair();

  if (pair.codeChallengeMethod !== 'S256') {
    problems.push(`挑战方法必须是 S256，实际为 ${String(pair.codeChallengeMethod)}（明文挑战等于没有 PKCE）`);
  }
  if (!/^[A-Za-z0-9_-]{43}$/.test(String(pair.codeVerifier))) {
    problems.push(`verifier 必须为 43 字符 base64url，实际 ${String(pair.codeVerifier).length} 字符`);
  }
  if (!/^[A-Za-z0-9_-]{43}$/.test(String(pair.codeChallenge))) {
    problems.push(`challenge 必须为 43 字符 base64url，实际 ${String(pair.codeChallenge).length} 字符`);
  }
  if (pair.codeChallenge !== referenceChallenge(pair.codeVerifier)) {
    problems.push('challenge 必须等于 base64url(sha256(verifier))');
  }
  if (pair.codeVerifier === second.codeVerifier) {
    problems.push('两次生成的 verifier 相同：随机源没有真正随机');
  }

  const state = impl.createState();
  const stateAgain = impl.createState();
  if (!/^[A-Za-z0-9_-]{43}$/.test(String(state))) {
    problems.push('state 必须为 43 字符 base64url');
  }
  if (state === stateAgain) {
    problems.push('两次生成的 state 相同：一次性随机值失效');
  }

  return problems;
}

/**
 * 断言 2：授权地址必须逐字带上全部必要参数。
 *
 * @param {object} impl - 被测实现
 * @returns {Promise<Array<string>>} 问题列表
 */
async function checkAuthorizeUrl(impl) {
  const problems = [];
  const state = impl.createState();
  const challenge = referenceChallenge(VERIFIER);
  const url = impl.buildAuthorizeUrl({
    issuer: ISSUER,
    clientId: CLIENT_ID,
    redirectUri: REDIRECT_URI,
    state,
    codeChallenge: challenge,
    scope: SCOPE,
  });

  const questionMark = url.indexOf('?');
  if (questionMark === -1) {
    problems.push('授权地址必须带 query');
    return problems;
  }
  const query = url.slice(questionMark + 1);

  const verbatim = [
    ['response_type=code', 'response_type=code'],
    ['code_challenge_method=S256', 'code_challenge_method=S256'],
    [`state=${state}`, `非空 state（state=${state}）`],
    [`code_challenge=${challenge}`, '正确的 code_challenge'],
    [`redirect_uri=${encodeURIComponent(REDIRECT_URI)}`, '与发起时一致的 redirect_uri'],
  ];
  for (const [needle, label] of verbatim) {
    if (!query.includes(needle)) problems.push(`授权地址缺少 ${label}`);
  }

  if (!url.startsWith(`${ISSUER.replace(/\/+$/, '')}/protocol/openid-connect/auth?`)) {
    problems.push('授权地址前缀不正确（应去掉 issuer 结尾斜杠后再拼接）');
  }
  if (url.includes('//protocol')) {
    problems.push('授权地址出现双斜杠：issuer 结尾斜杠未归一化');
  }

  let parsed = null;
  try {
    parsed = new URL(url);
  } catch {
    problems.push('授权地址不是合法 URL');
  }
  if (parsed) {
    if ((parsed.searchParams.get('state') ?? '') !== state) problems.push('授权地址里的 state 与传入值不一致');
    if ((parsed.searchParams.get('redirect_uri') ?? '') !== REDIRECT_URI) {
      problems.push('授权地址里的 redirect_uri 与传入值不一致');
    }
    if ((parsed.searchParams.get('code_challenge') ?? '') !== challenge) {
      problems.push('授权地址里的 code_challenge 与传入值不一致');
    }
    if ((parsed.searchParams.get('client_id') ?? '') !== CLIENT_ID) {
      problems.push('授权地址缺少 client_id');
    }
  }

  return problems;
}

/**
 * 断言 3：回跳参数四种输入都必须解析正确。
 *
 * @param {object} impl - 被测实现
 * @returns {Promise<Array<string>>} 问题列表
 */
async function checkParseCallback(impl) {
  const problems = [];

  const normal = impl.parseCallbackQuery('?code=CODE-1&state=STATE-1');
  if (normal.kind !== 'ok') {
    problems.push('正常情况下应解析为 ok');
  } else {
    if (normal.code !== 'CODE-1') problems.push('正常情况下的授权码解析错误');
    if (normal.state !== 'STATE-1') problems.push('正常情况下的 state 解析错误');
  }

  const denied = impl.parseCallbackQuery('?error=access_denied&state=STATE-1');
  if (denied.kind !== 'error' || denied.reason !== 'provider-error') {
    problems.push('服务端拒绝（error=access_denied）时应判为拒绝，不得当作正常');
  }

  const noCode = impl.parseCallbackQuery('?state=STATE-1');
  if (noCode.kind !== 'error' || noCode.reason !== 'missing-code') {
    problems.push('缺少 code 时应判为拒绝');
  }

  const noState = impl.parseCallbackQuery('?code=CODE-1');
  if (noState.kind !== 'error' || noState.reason !== 'missing-state') {
    problems.push('缺少 state 时应判为拒绝，不得当作正常');
  }

  const empty = impl.parseCallbackQuery('');
  if (empty.kind !== 'missing') {
    problems.push('空 query（用户直接打开本页）应判为 missing');
  }

  return problems;
}

/**
 * 断言 4：校验不通过时**一次兑换请求都不许发**。
 *
 * @param {object} impl - 被测实现
 * @returns {Promise<Array<string>>} 问题列表
 */
async function checkStateGuard(impl) {
  const problems = [];
  const pending = {
    state: 'STATE-REAL',
    codeVerifier: VERIFIER,
    redirectUri: REDIRECT_URI,
    createdAt: NOW,
  };

  /** 统计兑换调用次数的假 fetch */
  const makeFetch = () => {
    const counter = { calls: 0 };
    const fetchImpl = async () => {
      counter.calls += 1;
      return okResponse();
    };
    return { counter, fetchImpl };
  };

  // ① state 不匹配（伪造回跳）
  const forged = makeFetch();
  const forgedOutcome = await impl.completeAuthorization({
    search: '?code=CODE-1&state=STATE-FORGED',
    pending,
    issuer: ISSUER,
    clientId: CLIENT_ID,
    fetchImpl: forged.fetchImpl,
    now: NOW,
  });
  if (forgedOutcome.kind !== 'rejected' || forgedOutcome.reason !== 'mismatch') {
    problems.push('state 不匹配时必须判为拒绝');
  }
  if (forged.counter.calls !== 0) {
    problems.push(`state 不匹配时不得发起兑换，实际发起了 ${forged.counter.calls} 次`);
  }
  if (forgedOutcome.consumePending !== false) {
    problems.push('state 不匹配时不得写（或删）任何本地存储');
  }
  if (forgedOutcome.session !== null) {
    problems.push('被拒的结局不得携带会话');
  }

  // ② 本地状态缺失（不是本浏览器发起的登录）
  const noPending = makeFetch();
  const noPendingOutcome = await impl.completeAuthorization({
    search: '?code=CODE-1&state=STATE-REAL',
    pending: null,
    issuer: ISSUER,
    clientId: CLIENT_ID,
    fetchImpl: noPending.fetchImpl,
    now: NOW,
  });
  if (noPendingOutcome.kind !== 'rejected' || noPendingOutcome.reason !== 'no-pending') {
    problems.push('本地状态缺失时必须判为拒绝');
  }
  if (noPending.counter.calls !== 0) {
    problems.push('本地状态缺失时不得发起兑换');
  }

  // ③ 本地状态过期（11 分钟前创建）
  const expired = makeFetch();
  const expiredOutcome = await impl.completeAuthorization({
    search: '?code=CODE-1&state=STATE-REAL',
    pending: { ...pending, createdAt: NOW - 11 * 60 * 1000 },
    issuer: ISSUER,
    clientId: CLIENT_ID,
    fetchImpl: expired.fetchImpl,
    now: NOW,
  });
  if (expiredOutcome.kind !== 'rejected' || expiredOutcome.reason !== 'expired') {
    problems.push('本地状态过期时必须判为拒绝');
  }
  if (expired.counter.calls !== 0) {
    problems.push('本地状态过期时不得发起兑换');
  }

  // ④ 校验全通过：必须且只能兑换一次，并且带回会话；
  //    并且 `onValidated`（作废本地状态）必须**先于**兑换发生——否则「兑换挂在网络上、
  //    本地状态还留着」的那段时间就是一个可被并发重放的口子。
  const order = [];
  const happyFetch = async () => {
    order.push('exchange');
    return okResponse();
  };
  const happyOutcome = await impl.completeAuthorization({
    search: '?code=CODE-1&state=STATE-REAL',
    pending,
    issuer: ISSUER,
    clientId: CLIENT_ID,
    fetchImpl: happyFetch,
    now: NOW,
    onValidated: () => {
      order.push('validated');
    },
  });
  if (happyOutcome.kind !== 'authorized' || !happyOutcome.session) {
    problems.push('校验通过时应兑换成功并返回会话');
  }
  const exchangeTimes = order.filter((item) => item === 'exchange').length;
  if (exchangeTimes !== 1) {
    problems.push(`校验通过时应只兑换 1 次，实际 ${exchangeTimes} 次`);
  }
  if (order.join(',') !== 'validated,exchange') {
    problems.push(`校验通过后必须先作废本地状态再兑换，实际顺序为 [${order.join(',')}]`);
  }
  if (happyOutcome.consumePending !== true) {
    problems.push('校验通过后必须删除本地状态（一次性使用）');
  }

  // ⑤ 校验未通过时 onValidated 一次都不许触发（否则伪造链接能清掉正常登录）
  let rejectedValidated = 0;
  await impl.completeAuthorization({
    search: '?code=CODE-1&state=STATE-FORGED',
    pending,
    issuer: ISSUER,
    clientId: CLIENT_ID,
    fetchImpl: async () => okResponse(),
    now: NOW,
    onValidated: () => {
      rejectedValidated += 1;
    },
  });
  if (rejectedValidated !== 0) {
    problems.push('校验未通过时不得作废本地状态（不得触发 onValidated）');
  }

  return problems;
}

/**
 * 断言 5：兑换请求的方法 / 头 / 字段齐全，且不含客户端密钥。
 *
 * @param {object} impl - 被测实现
 * @returns {Promise<Array<string>>} 问题列表
 */
async function checkExchangeRequest(impl) {
  const problems = [];
  let captured = null;

  const fetchImpl = async (url, init) => {
    captured = { url, init };
    return okResponse();
  };

  const result = await impl.exchangeCode({
    issuer: ISSUER,
    clientId: CLIENT_ID,
    code: 'CODE-1',
    codeVerifier: VERIFIER,
    redirectUri: REDIRECT_URI,
    fetchImpl,
    now: NOW,
  });

  if (!captured) {
    problems.push('兑换请求根本没有发出');
    return problems;
  }

  if (result.kind !== 'ok' || !result.session) {
    problems.push('兑换成功时应返回会话');
  } else {
    if (result.session.access_token !== 'AT-fake') problems.push('会话里的访问令牌取值错误');
    if (result.session.refresh_token !== 'RT-fake') problems.push('会话里的续期令牌取值错误');
    if (!(result.session.expires_at > NOW)) problems.push('会话过期时刻必须在当前时刻之后');
  }

  if (String(captured.init?.method ?? '').toUpperCase() !== 'POST') {
    problems.push('兑换请求必须使用 POST');
  }
  const contentType = readHeader(captured.init, 'Content-Type').toLowerCase();
  if (!contentType.includes('application/x-www-form-urlencoded')) {
    problems.push('兑换请求的 Content-Type 必须是 application/x-www-form-urlencoded');
  }

  const body = String(captured.init?.body ?? '');
  if (body === '') {
    problems.push('兑换请求必须带表单 body');
  }
  const params = new URLSearchParams(body);
  const required = {
    grant_type: 'authorization_code',
    client_id: CLIENT_ID,
    code: 'CODE-1',
    redirect_uri: REDIRECT_URI,
    code_verifier: VERIFIER,
  };
  for (const [key, expected] of Object.entries(required)) {
    if ((params.get(key) ?? '') !== expected) problems.push(`兑换请求缺少或写错字段 ${key}`);
  }
  if (params.has('client_secret') || body.toLowerCase().includes('client_secret')) {
    problems.push('兑换请求不得携带 client_secret（公开客户端）');
  }
  if (!String(captured.url).endsWith('/protocol/openid-connect/token')) {
    problems.push('兑换请求的端点地址不正确');
  }

  return problems;
}

/**
 * 断言 6：兑换失败必须收敛为「未成功」，不得把异常抛给页面。
 *
 * 单独成条的原因：失败收敛是「用户会不会看到白屏 / 卡住」的分水岭，
 * 与请求形状是两类缺陷，混在一条里会让报告说不清到底错在哪。
 *
 * @param {object} impl - 被测实现
 * @returns {Promise<Array<string>>} 问题列表
 */
async function checkExchangeConvergence(impl) {
  const problems = [];

  // 网络异常（断网、DNS 失败）
  const throwing = await impl.exchangeCode({
    issuer: ISSUER,
    clientId: CLIENT_ID,
    code: 'CODE-1',
    codeVerifier: VERIFIER,
    redirectUri: REDIRECT_URI,
    fetchImpl: async () => {
      throw new Error('network down');
    },
    now: NOW,
  });
  if (throwing.kind !== 'failed') problems.push('网络异常时应返回 failed 而不是抛出');

  const rejected = await impl.exchangeCode({
    issuer: ISSUER,
    clientId: CLIENT_ID,
    code: 'CODE-1',
    codeVerifier: VERIFIER,
    redirectUri: REDIRECT_URI,
    fetchImpl: async () => ({ ok: false, status: 400, json: async () => ({}) }),
    now: NOW,
  });
  if (rejected.kind !== 'failed') problems.push('服务端拒绝时应返回 failed');

  return problems;
}

/**
 * 断言 7：本地状态 10 分钟过期。
 *
 * @param {object} impl - 被测实现
 * @returns {Promise<Array<string>>} 问题列表
 */
async function checkPendingTtl(impl) {
  const problems = [];
  const base = { state: 'S', codeVerifier: VERIFIER, redirectUri: REDIRECT_URI, createdAt: NOW };

  if (impl.isPendingExpired({ ...base, createdAt: NOW - 11 * 60 * 1000 }, NOW) !== true) {
    problems.push('11 分钟前创建必须判为过期');
  }
  if (impl.isPendingExpired({ ...base, createdAt: NOW - 9 * 60 * 1000 }, NOW) !== false) {
    problems.push('9 分钟前创建不应判为过期');
  }
  if (impl.isPendingExpired({ ...base, createdAt: NOW - 10 * 60 * 1000 }, NOW) !== true) {
    problems.push('恰好 10 分钟应判为过期（TTL 边界不得放行）');
  }
  if (impl.isPendingExpired({ ...base, createdAt: Number.NaN }, NOW) !== true) {
    problems.push('非法时间戳必须判为过期');
  }

  return problems;
}

/**
 * 造一个内存版 Storage（只实现用得到的三个方法）。
 *
 * @returns {object} Storage 替身
 */
function makeMemoryStorage() {
  const map = new Map();
  return {
    getItem(key) {
      return map.has(key) ? map.get(key) : null;
    },
    setItem(key, value) {
      map.set(key, String(value));
    },
    removeItem(key) {
      map.delete(key);
    },
  };
}

/**
 * 造一个「一碰就抛」的 Storage（模拟隐私模式 / 配额关闭）。
 *
 * @returns {object} Storage 替身
 */
function makeThrowingStorage() {
  const deny = () => {
    throw new Error('storage denied');
  };
  return { getItem: deny, setItem: deny, removeItem: deny };
}

/**
 * 断言 8：会话存储的容错与旧键兼容。
 *
 * 覆盖三件事：
 * - `expires_at: null`（开发令牌无过期信息）**不得**判为过期，否则一进来就被踢回登录页；
 * - 旧键 `officeai_access_token` 必须能迁移到新键并删除旧键（避免已登录的开发环境掉线）；
 * - 存储不可用 / 内容损坏时必须降级为 null / false，不得抛异常、不得把脏数据当登录态。
 *
 * @returns {Promise<Array<string>>} 问题列表
 */
async function checkSessionStore() {
  const problems = [];
  const store = await import(pathToFileURL(SESSION_STORE_PATH).href);

  const local = makeMemoryStorage();
  const session = makeMemoryStorage();
  globalThis.window = { localStorage: local, sessionStorage: session };

  const noExpiry = { access_token: 'T-plain', refresh_token: null, expires_at: null };
  if (store.isSessionExpired(noExpiry, NOW) !== false) {
    problems.push('expires_at 为 null（无过期信息）时不得判为过期');
  }
  if (store.isSessionExpired({ ...noExpiry, expires_at: NOW - 1 }, NOW) !== true) {
    problems.push('有 expires_at 且已过期时必须判为过期');
  }
  if (store.isSessionExpired({ ...noExpiry, expires_at: Number.NaN }, NOW) !== true) {
    problems.push('非法 expires_at 必须判为过期');
  }

  if (store.saveSession(noExpiry) !== true) problems.push('会话必须能落库');
  const loaded = store.loadSession();
  if (!loaded || loaded.access_token !== 'T-plain') problems.push('会话必须能原样读回');

  // 旧键迁移（临时兼容）
  local.removeItem(store.SESSION_STORAGE_KEY);
  local.setItem(store.LEGACY_SESSION_STORAGE_KEY, 'T-legacy');
  const migrated = store.loadSession();
  if (!migrated || migrated.access_token !== 'T-legacy') problems.push('旧键里的会话必须能迁移读取');
  if (local.getItem(store.LEGACY_SESSION_STORAGE_KEY) !== null) {
    problems.push('迁移完成后必须删除旧键（否则会反复迁移）');
  }
  if (!local.getItem(store.SESSION_STORAGE_KEY)) problems.push('迁移完成后必须写入新键');
  if (migrated && store.isSessionExpired(migrated, NOW) !== false) {
    problems.push('迁移出的会话没有过期信息，不得判为过期');
  }

  // 脏数据：不能当登录态
  local.setItem(store.SESSION_STORAGE_KEY, '{"access_token":""}');
  if (store.loadSession() !== null) problems.push('形状不对的会话必须当作没有会话');

  // 存储不可用：降级，不抛
  globalThis.window = { localStorage: makeThrowingStorage(), sessionStorage: makeThrowingStorage() };
  if (store.loadSession() !== null) problems.push('存储抛异常时读取必须降级为 null');
  if (store.saveSession(noExpiry) !== false) problems.push('存储抛异常时写入必须返回 false');
  if (store.loadPending() !== null) problems.push('存储抛异常时读取待完成登录必须降级为 null');

  return problems;
}

/** 全部断言（顺序固定，输出可读） */
const CHECKS = [
  { id: 'PKCE-S256', title: 'PKCE 必须 S256：challenge = base64url(sha256(verifier))', run: checkPkce },
  { id: 'AUTHORIZE-URL', title: '授权地址 query 逐字包含必要参数', run: checkAuthorizeUrl },
  { id: 'CALLBACK-PARSE', title: '回跳参数解析（正常/拒绝/缺授权码/缺随机值）', run: checkParseCallback },
  { id: 'STATE-GUARD', title: '校验不通过时不得发起兑换', run: checkStateGuard },
  { id: 'EXCHANGE-REQUEST', title: '兑换请求方法/头/字段齐全且不含客户端密钥', run: checkExchangeRequest },
  { id: 'EXCHANGE-FAILURE', title: '兑换失败收敛为未成功（不向页面抛异常）', run: checkExchangeConvergence },
  { id: 'PENDING-TTL', title: '本地状态 10 分钟过期', run: checkPendingTtl },
  { id: 'SESSION-STORE', title: '会话存储：无过期信息不判过期 + 旧键迁移 + 降级不抛', run: checkSessionStore },
];

/**
 * 跑全部断言。
 *
 * @param {object} impl - 被测实现
 * @returns {Promise<Array<{id: string, title: string, problems: Array<string>}>>} 结果
 */
async function runChecks(impl) {
  const results = [];
  for (const check of CHECKS) {
    let problems;
    try {
      problems = await check.run(impl);
    } catch (error) {
      problems = [`断言抛出异常：${error instanceof Error ? error.message : String(error)}`];
    }
    results.push({ id: check.id, title: check.title, problems });
  }
  return results;
}

// ---------------------------------------------------------------------------
// 自检：故意写错的实现必须被抓到
// ---------------------------------------------------------------------------

/**
 * 负样本：每个桩只写错一处，门禁必须至少报出一条含 `expect` 关键字的问题。
 *
 * 这些桩不是「复制实现」，而是在真实实现上做最小篡改，模拟最容易发生的
 * 退化（明文挑战、漏参数、跳过校验、带密钥、永不过期、用 GET）。
 */
const MUTANTS = [
  {
    name: '把 S256 换成明文挑战',
    expect: 'S256',
    patch: (impl) => ({
      ...impl,
      createPkcePair: async () => {
        const verifier = impl.createState();
        return { codeVerifier: verifier, codeChallenge: verifier, codeChallengeMethod: 'plain' };
      },
    }),
  },
  {
    name: '授权地址漏掉非空 state',
    expect: 'state',
    patch: (impl) => ({
      ...impl,
      buildAuthorizeUrl: (options) =>
        `${String(options.issuer).replace(/\/+$/, '')}/protocol/openid-connect/auth?` +
        new URLSearchParams({
          client_id: options.clientId,
          redirect_uri: options.redirectUri,
          response_type: 'code',
          scope: options.scope,
          code_challenge: options.codeChallenge,
          code_challenge_method: 'S256',
        }).toString(),
    }),
  },
  {
    name: '把「缺少一次性随机值」当成正常回跳',
    expect: 'state',
    patch: (impl) => ({
      ...impl,
      parseCallbackQuery: (search) => {
        const real = impl.parseCallbackQuery(search);
        if (real.kind === 'error' && real.reason === 'missing-state') {
          return { kind: 'ok', code: 'CODE-X', state: 'STATE-X' };
        }
        return real;
      },
    }),
  },
  {
    name: '兑换之后才作废本地状态',
    expect: '先作废本地状态',
    patch: (impl) => ({
      ...impl,
      completeAuthorization: async (options) => {
        const now = options.now ?? Date.now();
        const parsed = impl.parseCallbackQuery(options.search);
        if (parsed.kind !== 'ok') {
          return { kind: 'rejected', reason: 'callback', session: null, consumePending: false };
        }
        if (!options.pending) {
          return { kind: 'rejected', reason: 'no-pending', session: null, consumePending: false };
        }
        if (impl.isPendingExpired(options.pending, now)) {
          return { kind: 'rejected', reason: 'expired', session: null, consumePending: true };
        }
        if (parsed.state !== options.pending.state) {
          return { kind: 'rejected', reason: 'mismatch', session: null, consumePending: false };
        }
        const result = await impl.exchangeCode({
          issuer: options.issuer,
          clientId: options.clientId,
          code: parsed.code,
          codeVerifier: options.pending.codeVerifier,
          redirectUri: options.pending.redirectUri,
          fetchImpl: options.fetchImpl,
          now,
        });
        // 故意写错：兑换完才作废本地状态
        options.onValidated?.();
        return result.kind === 'ok'
          ? { kind: 'authorized', reason: null, session: result.session, consumePending: true }
          : { kind: 'rejected', reason: 'exchange', session: null, consumePending: true };
      },
    }),
  },
  {
    name: '跳过一次性随机值比对直接兑换',
    expect: 'state',
    patch: (impl) => ({
      ...impl,
      completeAuthorization: async (options) => {
        const parsed = impl.parseCallbackQuery(options.search);
        if (parsed.kind !== 'ok') {
          return { kind: 'rejected', reason: 'callback', session: null, consumePending: false };
        }
        const result = await impl.exchangeCode({
          issuer: options.issuer,
          clientId: options.clientId,
          code: parsed.code,
          codeVerifier: options.pending?.codeVerifier ?? '',
          redirectUri: options.pending?.redirectUri ?? '',
          fetchImpl: options.fetchImpl,
          now: options.now,
        });
        return result.kind === 'ok'
          ? { kind: 'authorized', reason: null, session: result.session, consumePending: true }
          : { kind: 'rejected', reason: 'exchange', session: null, consumePending: true };
      },
    }),
  },
  {
    name: '兑换时带上客户端密钥',
    expect: 'client_secret',
    patch: (impl) => ({
      ...impl,
      exchangeCode: async (options) => {
        const body = new URLSearchParams({
          grant_type: 'authorization_code',
          client_id: options.clientId,
          code: options.code,
          redirect_uri: options.redirectUri,
          code_verifier: options.codeVerifier,
          client_secret: 'leaked',
        });
        const response = await options.fetchImpl(
          `${String(options.issuer).replace(/\/+$/, '')}/protocol/openid-connect/token`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: body.toString(),
          },
        );
        const payload = await response.json();
        return {
          kind: 'ok',
          session: {
            access_token: payload.access_token,
            refresh_token: payload.refresh_token ?? null,
            expires_at: (options.now ?? Date.now()) + 300_000,
          },
        };
      },
    }),
  },
  {
    name: '用 GET 兑换（参数进地址栏）',
    expect: 'POST',
    patch: (impl) => ({
      ...impl,
      exchangeCode: async (options) => {
        const body = new URLSearchParams({
          grant_type: 'authorization_code',
          client_id: options.clientId,
          code: options.code,
          redirect_uri: options.redirectUri,
          code_verifier: options.codeVerifier,
        });
        await options.fetchImpl(
          `${String(options.issuer).replace(/\/+$/, '')}/protocol/openid-connect/token?${body.toString()}`,
          { method: 'GET', headers: { Accept: 'application/json' } },
        );
        return {
          kind: 'ok',
          session: { access_token: 'AT-fake', refresh_token: null, expires_at: (options.now ?? Date.now()) + 300_000 },
        };
      },
    }),
  },
  {
    name: '本地状态永不过期',
    expect: '过期',
    patch: (impl) => ({
      ...impl,
      isPendingExpired: () => false,
    }),
  },
];

/**
 * 自检。
 *
 * @param {object} real - 真实实现
 * @returns {Promise<number>} 0 失败 / 1 通过（故意：通过=1，让门禁「失效即红」）
 */
async function selfTest(real) {
  const failures = [];

  const realResults = await runChecks(real);
  const realProblems = realResults.filter((item) => item.problems.length > 0);
  if (realProblems.length > 0) {
    failures.push('真实实现本身就不满足断言，自检无法给出有意义结论');
    for (const item of realProblems) {
      failures.push(`  ${item.id}: ${item.problems.join('；')}`);
    }
  }

  for (const mutant of MUTANTS) {
    const results = await runChecks(mutant.patch(real));
    const allProblems = results.flatMap((item) => item.problems);
    const caught = allProblems.some((problem) => problem.includes(mutant.expect));
    if (!caught) {
      failures.push(
        `负样本漏抓「${mutant.name}」：期望命中「${mutant.expect}」，实际 ${allProblems.join('；') || '无问题'}`,
      );
    }
  }

  if (failures.length > 0) {
    console.error(`SELF-TEST FAIL（${failures.length}）`);
    for (const failure of failures) console.error('  -', failure);
    return 0;
  }

  console.info(
    `SELF-TEST PASS：真实实现 0 问题；负样本 ${MUTANTS.length}/${MUTANTS.length} 全部抓到` +
      `（明文挑战/漏参数/跳过校验/带密钥/GET 兑换/永不过期/兑换后才作废）`,
  );
  return 1;
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

/**
 * 装载被测模块。
 *
 * @returns {Promise<object>} 模块命名空间
 * @throws Error 模块不存在或无法装载时抛出
 */
async function loadImplementation() {
  try {
    return await import(pathToFileURL(OIDC_MODULE_PATH).href);
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    throw new Error(`无法装载 ${OIDC_MODULE_PATH}：${detail}`);
  }
}

/**
 * 入口。
 *
 * @param {Array<string>} argv - 命令行参数
 * @returns {Promise<number>} 退出码
 */
async function main(argv) {
  const known = ['--self-test', '--json'];
  const unknown = argv.filter((item) => !known.includes(item));
  if (unknown.length > 0) {
    console.error(`[FAIL] 未知参数：${unknown.join(' ')}（可用：${known.join(' ')}）`);
    return 2;
  }

  let real;
  try {
    real = await loadImplementation();
  } catch (error) {
    console.error(`[FAIL] ${error instanceof Error ? error.message : String(error)}`);
    return 2;
  }

  if (argv.includes('--self-test')) return selfTest(real);

  const results = await runChecks(real);
  const failed = results.filter((item) => item.problems.length > 0);

  if (argv.includes('--json')) {
    console.info(JSON.stringify(results, null, 2));
  } else {
    for (const item of results) {
      if (item.problems.length === 0) {
        console.info(`[OK] ${item.id} ${item.title}`);
      } else {
        console.info(`[FAIL] ${item.id} ${item.title}`);
        for (const problem of item.problems) console.info(`       - ${problem}`);
      }
    }
  }

  if (failed.length > 0) {
    console.info(`登录闭环门禁：${results.length - failed.length}/${results.length} 通过，${failed.length} 项不满足`);
    return 1;
  }

  console.info(`登录闭环门禁：${results.length}/${results.length} 全部通过（PKCE S256 直连 + 校验先行）`);
  return 0;
}

process.exit(await main(process.argv.slice(2)));
