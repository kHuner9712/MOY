#!/usr/bin/env node
/**
 * 登录闭环门禁（P0-5 路线 A：令牌不进 JavaScript）
 * ---------------------------------------------------------------------------
 * 依据：本轮改造把「浏览器持有令牌」整条链路拆掉 —— 令牌只以 httpOnly Cookie
 * 下发，前端不存、不读、不注入。这是一条**安全属性**，靠人工 review 守不住
 * （新增一个页面就可能把令牌又写回本地存储），因此做成静态门禁：
 *
 * 1. `src/` 下除「历史键清理」外，不得出现本地存储 / 会话级存储 / Cookie 读写 API；
 * 2. 不得再出现 `setAccessToken` / `getAccessToken`（令牌内存缓存的入口）；
 * 3. 不得注入 `Authorization` 请求头（身份只走 Cookie）；
 * 4. `lib/session-client.ts` 必须齐备五个端点与会话凭据选项（`include` + `no-store`）；
 * 5. `lib/api-client.ts` 的每一次 `fetch` 都必须带 `credentials: 'include'`
 *    （漏一处就是「登录了但每个请求都匿名」）；
 * 6. `lib/auth-refresh.ts` 必须保留四分类判据与抖动/限次常量
 *    （删掉任何一个，服务端刚修掉的三类缺陷就会从客户端回来）；
 * 7. 浏览器侧不得再生成 PKCE、不得兑换令牌（`lib/oidc.ts` 只留回跳解析）；
 * 8. `lib/session-store.ts` 只删不写（`removeItem` 在、`setItem`/`getItem` 不在）。
 *
 * 用法（在 apps/web 下执行）：
 *   node scripts/check-oidc-auth.mjs               # 全量断言
 *   node scripts/check-oidc-auth.mjs --json        # 输出 JSON（供 CI/报告消费）
 *   node scripts/check-oidc-auth.mjs --self-test   # 负样本自检（通过时返回 1）
 *
 * 退出码（与禁词 / 占位词 / 页面三态扫描器保持同一套约定，**故意如此**）：
 *   0 = 全部满足
 *   1 = 有断言不满足；`--self-test` 下 1 表示**自检通过**（故意写错的实现全被抓到）
 *   2 = 参数 / 文件错误（例如 `src` 不存在）
 *
 * 设计要点：
 * - 断言跑的是**真实源码文本**（不是另写一份实现来「自我印证」）；
 * - 行为口径（三态判据四组、四分类、抖动区间）由 vitest 覆盖
 *   （`src/lib/auth-refresh.test.ts`），本脚本只守「结构不会被悄悄改回去」；
 * - `--self-test` 用若干「故意写错」的源码注入证明门禁会红 —— 门禁失效必须能被发现。
 */

import { readdir, readFile } from 'node:fs/promises';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = resolve(HERE, '..');
const SRC_ROOT = resolve(WEB_ROOT, 'src');

/** 允许读本地存储的唯一文件（历史键一次性清理） */
const LEGACY_CLEANUP_FILE = 'src/lib/session-store.ts';

/** 断言失败（区别于脚本自身出错） */
class Violation extends Error {}

/**
 * 声明一条规则。
 *
 * @param {string} id - 规则标识（出现在输出里）
 * @param {string} title - 规则说明（业务语言）
 * @param {(files: Record<string, string>) => void} run - 断言体；不满足时抛 Violation
 * @returns {{ id: string, title: string, run: (files: Record<string, string>) => void }} 规则对象
 */
function rule(id, title, run) {
  return { id, title, run };
}

/**
 * 去掉源码里的注释（保留字符串内容）。
 *
 * 为什么必须先去注释：本仓库大量注释在**解释为什么删掉**某个 API，
 * 直接 grep 会把「说明文字」误判成「还在用」。
 * `//` 前紧跟 `:` 时视为 URL（`https://...`），不当作行注释起点。
 *
 * @param {string} source - 原始源码
 * @returns {string} 去掉注释后的源码
 */
function stripComments(source) {
  const withoutBlock = source.replace(/\/\*[\s\S]*?\*\//g, '');
  return withoutBlock
    .split('\n')
    .map((line) => {
      const index = line.indexOf('//');
      if (index === -1) return line;
      if (line.slice(0, index).trimEnd().endsWith(':')) return line;
      return line.slice(0, index);
    })
    .join('\n');
}

/**
 * 统计子串出现次数。
 *
 * @param {string} haystack - 被搜索文本
 * @param {string} needle - 子串
 * @returns {number} 出现次数
 */
function countOf(haystack, needle) {
  if (needle === '') return 0;
  let count = 0;
  let index = haystack.indexOf(needle);
  while (index !== -1) {
    count += 1;
    index = haystack.indexOf(needle, index + needle.length);
  }
  return count;
}

/** 全部规则（顺序即输出顺序） */
const RULES = [
  rule(
    'no-client-storage',
    'src 下不得读写本地存储 / 会话存储 / Cookie（历史键清理除外）',
    (files) => {
      const offenders = [];
      for (const [path, raw] of Object.entries(files)) {
        if (path === LEGACY_CLEANUP_FILE) continue;
        const code = stripComments(raw);
        if (/localStorage|sessionStorage|document\.cookie/.test(code)) {
          offenders.push(path);
        }
      }
      if (offenders.length > 0) {
        throw new Violation(`令牌可能被写进可被脚本读取的存储：${offenders.join(', ')}`);
      }
    },
  ),

  rule('no-token-api', '不得再出现 setAccessToken / getAccessToken（令牌内存缓存）', (files) => {
    const offenders = [];
    for (const [path, raw] of Object.entries(files)) {
      const code = stripComments(raw);
      if (/setAccessToken|getAccessToken/.test(code)) {
        offenders.push(path);
      }
    }
    if (offenders.length > 0) {
      throw new Violation(`令牌读写接口仍在：${offenders.join(', ')}`);
    }
  }),

  rule('no-authorization-header', '不得注入 Authorization 请求头（身份只走 Cookie）', (files) => {
    const offenders = [];
    for (const [path, raw] of Object.entries(files)) {
      const code = stripComments(raw);
      if (/headers\.(?:set|append)\(\s*['"]authorization['"]/i.test(code)) {
        offenders.push(`${path}（设置 Authorization 头）`);
      }
      if (/Authorization['"]?\s*[:,]\s*`?Bearer/i.test(code)) {
        offenders.push(`${path}（拼 Bearer 串）`);
      }
    }
    if (offenders.length > 0) {
      throw new Violation(`仍存在基于请求头的身份注入：${offenders.join(', ')}`);
    }
  }),

  rule(
    'session-client-shape',
    'lib/session-client.ts 必须齐备五端点与 Cookie 凭据选项',
    (files) => {
      const text = files['src/lib/session-client.ts'];
      if (!text) {
        throw new Violation('缺少 src/lib/session-client.ts');
      }
      const code = stripComments(text);
      const required = [
        'startLogin',
        'completeLogin',
        'fetchSession',
        'refreshSession',
        'logoutSession',
      ];
      const missing = required.filter(
        (name) => !new RegExp(`export\\s+async\\s+function\\s+${name}`).test(code),
      );
      if (missing.length > 0) {
        throw new Violation(`会话端点函数缺失：${missing.join(', ')}`);
      }
      if (!/['"]\/sso['"]/.test(code)) {
        throw new Violation('会话端点前缀不是 /sso（不能挂在 /api/v1 下）');
      }
      const fetchCount = countOf(code, 'fetch(');
      const credentialCount = countOf(code, "credentials: 'include'");
      if (credentialCount < 1) {
        throw new Violation("缺少 credentials: 'include'（浏览器不会带 httpOnly Cookie）");
      }
      if (fetchCount !== credentialCount) {
        throw new Violation(`有 ${fetchCount} 处 fetch，却只有 ${credentialCount} 处 Cookie 凭据`);
      }
      if (countOf(code, "cache: 'no-store'") < 1) {
        throw new Violation("缺少 cache: 'no-store'（会话状态不可缓存）");
      }
    },
  ),

  rule('api-client-cookie', 'lib/api-client.ts 的每个 fetch 都必须带 Cookie 凭据', (files) => {
    const text = files['src/lib/api-client.ts'];
    if (!text) {
      throw new Violation('缺少 src/lib/api-client.ts');
    }
    const code = stripComments(text);
    const fetchCount = countOf(code, 'fetch(');
    const credentialCount = countOf(code, "credentials: 'include'");
    if (fetchCount === 0) {
      throw new Violation('lib/api-client.ts 里没有任何 fetch 调用（结构异常）');
    }
    if (credentialCount < fetchCount) {
      throw new Violation(
        `有 ${fetchCount} 处 fetch，却只有 ${credentialCount} 处 credentials: 'include'`,
      );
    }
  }),

  rule('auth-refresh-shape', 'lib/auth-refresh.ts 必须保留四分类判据与抖动/限次常量', (files) => {
    const text = files['src/lib/auth-refresh.ts'];
    if (!text) {
      throw new Violation('缺少 src/lib/auth-refresh.ts');
    }
    const code = stripComments(text);
    for (const name of [
      'decideSession',
      'shouldRefresh',
      'classifyRefreshFailure',
      'refreshWithRetry',
      'nextRefreshDelay',
    ]) {
      if (!code.includes(name)) {
        throw new Violation(`刷新策略缺少 ${name}`);
      }
    }
    // 四分类：死透（401 / 20002）与可重试（20006 / 30001 / 30002）都必须在场
    for (const token of [
      '401',
      '20002',
      '20006',
      '30001',
      '30002',
      'JITTER_MAX_MS',
      'MAX_REFRESH_RETRIES',
    ]) {
      if (!code.includes(token)) {
        throw new Violation(`刷新策略缺少判据/常量 ${token}`);
      }
    }
    if (!code.includes('统一登录未启用')) {
      throw new Violation('缺少「统一登录未启用」的放弃判据（stub 环境会打成死循环）');
    }
  }),

  rule('oidc-no-pkce', '浏览器侧不得再生成 PKCE 或兑换令牌', (files) => {
    const forbidden = ['createPkcePair', 'exchangeCode', 'code_verifier', 'code_challenge'];
    const offenders = [];
    for (const [path, raw] of Object.entries(files)) {
      const code = stripComments(raw);
      for (const token of forbidden) {
        if (code.includes(token)) {
          offenders.push(`${path}（${token}）`);
        }
      }
    }
    if (offenders.length > 0) {
      throw new Violation(`PKCE / 令牌兑换残留：${offenders.join(', ')}`);
    }
    const oidc = files['src/lib/oidc.ts'];
    if (!oidc || !stripComments(oidc).includes('parseCallbackQuery')) {
      throw new Violation('src/lib/oidc.ts 必须保留 parseCallbackQuery（回跳页要用）');
    }
  }),

  rule('session-store-delete-only', 'lib/session-store.ts 只删不写', (files) => {
    const text = files[LEGACY_CLEANUP_FILE];
    if (!text) {
      throw new Violation(`缺少 ${LEGACY_CLEANUP_FILE}`);
    }
    const code = stripComments(text);
    if (!code.includes('removeItem')) {
      throw new Violation('历史键清理逻辑缺失（旧令牌会长期滞留客户端）');
    }
    if (/\bsetItem\s*\(/.test(code) || /\bgetItem\s*\(/.test(code)) {
      throw new Violation('session-store 仍在写/读存储（应只删历史键）');
    }
  }),
];

/**
 * 读取 `src` 下的全部非测试源码。
 *
 * @returns {Promise<Record<string, string>>} 相对仓库根的路径 → 源码
 */
async function loadSources() {
  /** @type {Record<string, string>} */
  const files = {};

  /**
   * 递归遍历目录。
   *
   * @param {string} dir - 目录绝对路径
   * @returns {Promise<void>}
   */
  const walk = async (dir) => {
    const entries = await readdir(dir, { withFileTypes: true });
    for (const entry of entries) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) {
        await walk(full);
        continue;
      }
      if (!/\.(ts|tsx)$/.test(entry.name)) continue;
      if (/\.test\.(ts|tsx)$/.test(entry.name)) continue;
      const key = relative(WEB_ROOT, full).replace(/\\/g, '/');
      files[key] = await readFile(full, 'utf8');
    }
  };

  await walk(SRC_ROOT);
  return files;
}

/**
 * 跑一遍全部规则。
 *
 * @param {Record<string, string>} files - 源码快照
 * @returns {Array<{ id: string, title: string, ok: boolean, detail: string }>} 结果
 */
function runRules(files) {
  return RULES.map((item) => {
    try {
      item.run(files);
      return { id: item.id, title: item.title, ok: true, detail: '' };
    } catch (error) {
      if (error instanceof Violation) {
        return { id: item.id, title: item.title, ok: false, detail: error.message };
      }
      throw error;
    }
  });
}

/**
 * 自检：给每条规则喂「故意写错」的源码，必须都被抓出来。
 *
 * @param {Record<string, string>} files - 真实源码快照（作为基底）
 * @returns {{ caught: string[], missed: string[] }} 抓到 / 漏掉的规则
 */
function selfTest(files) {
  /** 每条规则的「坏样本」构造器：返回期望被拒的源码快照 */
  const breakers = {
    'no-client-storage': () => ({
      ...files,
      'src/lib/evil.ts': "export const t = window.localStorage.getItem('access_token');",
    }),
    'no-token-api': () => ({
      ...files,
      'src/lib/evil.ts': 'export function setAccessToken(token: string): void { void token; }',
    }),
    'no-authorization-header': () => ({
      ...files,
      'src/lib/evil.ts': "const h = new Headers(); h.set('Authorization', `Bearer ${token}`);",
    }),
    'session-client-shape': () => ({
      ...files,
      'src/lib/session-client.ts':
        'export async function startLogin(): Promise<string> { return ""; }',
    }),
    'api-client-cookie': () => ({
      ...files,
      'src/lib/api-client.ts': "const r = await fetch('/api/v1/x', { cache: 'no-store' });",
    }),
    'auth-refresh-shape': () => ({
      ...files,
      'src/lib/auth-refresh.ts': 'export function decideSession(): string { return "use"; }',
    }),
    'oidc-no-pkce': () => ({
      ...files,
      'src/lib/oidc.ts': 'export async function createPkcePair(): Promise<void> {}',
    }),
    'session-store-delete-only': () => ({
      ...files,
      [LEGACY_CLEANUP_FILE]:
        "export function keep(): void { window.localStorage.setItem('a', 'b'); }",
    }),
  };

  const caught = [];
  const missed = [];
  for (const item of RULES) {
    const breaker = breakers[item.id];
    if (!breaker) {
      missed.push(`${item.id}（缺少自检样本）`);
      continue;
    }
    const results = runRules(breaker());
    const target = results.find((entry) => entry.id === item.id);
    if (target && target.ok === false) {
      caught.push(item.id);
    } else {
      missed.push(item.id);
    }
  }
  return { caught, missed };
}

/**
 * 入口。
 *
 * @returns {Promise<void>}
 */
async function main() {
  const args = process.argv.slice(2);
  const asJson = args.includes('--json');
  const wantSelfTest = args.includes('--self-test');

  /** @type {Record<string, string>} */
  let files;
  try {
    files = await loadSources();
  } catch (error) {
    process.stderr.write(
      `无法读取 src 源码：${error instanceof Error ? error.message : String(error)}\n`,
    );
    process.exit(2);
    return;
  }

  if (wantSelfTest) {
    const { caught, missed } = selfTest(files);
    if (missed.length === 0) {
      process.stdout.write(`门禁自检通过：${caught.length} 条规则全部抓到了故意写错的样本\n`);
      // 与既有扫描器一致：自检通过返回 1（表示「负样本被检出」）
      process.exit(1);
      return;
    }
    process.stdout.write(`门禁自检失败：以下规则漏掉了坏样本 —— ${missed.join(', ')}\n`);
    process.exit(0);
    return;
  }

  const results = runRules(files);
  const failed = results.filter((entry) => !entry.ok);

  if (asJson) {
    process.stdout.write(`${JSON.stringify({ ok: failed.length === 0, results }, null, 2)}\n`);
  } else {
    for (const entry of results) {
      process.stdout.write(`${entry.ok ? 'PASS' : 'FAIL'}  ${entry.id}  ${entry.title}\n`);
      if (!entry.ok) {
        process.stdout.write(`      ↳ ${entry.detail}\n`);
      }
    }
    process.stdout.write(
      failed.length === 0
        ? `\n令牌不进 JavaScript：${results.length} 条结构断言全部满足\n`
        : `\n${failed.length} 条断言不满足（共 ${results.length} 条）\n`,
    );
  }

  process.exit(failed.length === 0 ? 0 : 1);
}

await main();
