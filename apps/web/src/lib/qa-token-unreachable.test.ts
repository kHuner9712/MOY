/**
 * QA 独立探针 1：**令牌在前端确实不可达**（P0-5 路线 A）。
 *
 * 与工程师门禁脚本 `scripts/check-oidc-auth.mjs` 的区别（刻意用不同方法，避免同一个
 * 盲区被验两遍）：
 * - 门禁脚本走**文本 grep + 去注释**：`'local' + 'Storage'`、`window['localStorage']`、
 *   `String.fromCharCode(...)`、`//` 出现在字符串里这四类都会骗过它；
 * - 本文件走**TypeScript AST 遍历**：拿到的是编译器认的语法节点，字符串字面量、
 *   元素访问、二元拼接、属性名都在节点层面判定，注释根本不是节点，天然免疫。
 *
 * 覆盖六类可达路径（任何一类漏掉，令牌就不是真的不可达）：
 * 1. 存储读写：`localStorage` / `sessionStorage`（含 `window[...]` 计算属性访问）；
 * 2. Cookie 读取：`document.cookie` 与 Cookie Store API（`cookieStore`）；
 * 3. 动态求值：`eval` / `new Function`（可以绕过一切静态字符串检查）；
 * 4. 令牌字面量：`access_token` / `refresh_token` / `id_token` / `accessToken` / `jwt`；
 * 5. 字符串拼接混淆：`'local' + 'Storage'` 这类把敏感词拆成两段；
 * 6. 内存缓存：模块级变量名里出现 token/bearer/credential —— 改造删掉了
 *    `accessToken`，但换一个名字存回内存同样等于「JS 可读」。
 *
 * 另外三条接口面断言（静态部分）：
 * - 五端点客户端的**函数签名**里没有令牌字段（`session-client.ts`）；
 * - `session-store.ts` 只删不写（`removeItem` 在，`setItem`/`getItem` 不在）；
 * - 全量 `src` 的非测试源码里，`Bearer` 只出现在注释里（注释不是 AST 节点）。
 *
 * 动态部分（「函数会不会把令牌返回出去」）在
 * `session-client-qa.test.ts` 里用桩响应体实测，不在本文件。
 */

import { readFileSync, readdirSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';

import ts from 'typescript';
import { describe, expect, it } from 'vitest';

/**
 * `src` 根目录。
 *
 * 不用 `import.meta.url`：本测试跑在 jsdom 环境里，`import.meta.url` 不是 file
 * 协议地址（`fileURLToPath` 会抛 "The URL must be of scheme file"）。
 * vitest 的 `root` 就是配置文件所在目录（`apps/web`），因此用 `process.cwd()`。
 */
const SRC_ROOT = resolve(process.cwd(), 'src');

/** 允许触碰浏览器存储的唯一文件（历史令牌键一次性清理，只删不读不写） */
const LEGACY_CLEANUP_FILE = 'lib/session-store.ts';

/**
 * 单条 AST 断言的超时（毫秒）。
 *
 * 解析 90 个源文件的 AST 在本机约 1s；但全量回归时 68 个测试文件并行 + v8 覆盖率
 * 插桩会把这段时间放大到 5s 上下（实测偶发踩到 vitest 默认 5s 上限）。
 * 这是**机器负载**而不是产品缺陷，因此显式放宽——并配上 memo，只算一次。
 */
const SCAN_TIMEOUT_MS = 30_000;

/** `src` 源码快照（只读一次） */
let cachedSources: SourceFile[] | null = null;

/**
 * 取 `src` 源码快照（memo）。
 *
 * @returns 待扫描文件清单
 */
function sources(): SourceFile[] {
  cachedSources ??= collectSources(SRC_ROOT);
  return cachedSources;
}

/** 全量扫描结果（memo，只跑一次） */
let cachedFindings: Finding[] | null = null;

/**
 * 取全量扫描结果（memo）。
 *
 * @returns 全部发现
 */
function findings(): Finding[] {
  cachedFindings ??= sources().flatMap(scanFile);
  return cachedFindings;
}

/** 一条静态发现 */
interface Finding {
  /** 相对 `src` 的文件路径（正斜杠） */
  file: string;
  /** 1 起的行号 */
  line: number;
  /** 命中的规则标识 */
  rule: string;
  /** 命中的源码片段（截断到 80 字符） */
  snippet: string;
}

/** 一个待扫描的源码文件 */
interface SourceFile {
  /** 相对 `src` 的路径（正斜杠） */
  path: string;
  /** 绝对路径 */
  absolutePath: string;
  /** 源码全文 */
  text: string;
}

/**
 * 递归收集 `src` 下的非测试源码。
 *
 * 排除 `.test.ts(x)` 与 `.d.ts`：前者是测试（门禁与工程师口径一致），
 * 后者没有可执行语句。
 *
 * @param root - `src` 绝对路径
 * @returns 待扫描文件清单（按路径排序，输出稳定）
 */
function collectSources(root: string): SourceFile[] {
  const collected: SourceFile[] = [];

  const walk = (dir: string): void => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const absolute = join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(absolute);
        continue;
      }
      if (!/\.(ts|tsx)$/.test(entry.name)) continue;
      if (/\.test\.(ts|tsx)$/.test(entry.name)) continue;
      if (/\.d\.ts$/.test(entry.name)) continue;
      collected.push({
        path: relative(root, absolute).replace(/\\/g, '/'),
        absolutePath: absolute,
        text: readFileSync(absolute, 'utf8'),
      });
    }
  };

  walk(root);
  return collected.sort((left, right) => left.path.localeCompare(right.path));
}

/** 敏感子串（出现即说明「令牌可能被脚本取到」这条路径没有被拆掉） */
const SENSITIVE_SUBSTRINGS: readonly string[] = [
  'localstorage',
  'sessionstorage',
  'document.cookie',
  'cookiestore',
  'access_token',
  'refresh_token',
  'id_token',
  'accesstoken',
  'refreshtoken',
  'idtoken',
  'authorization',
  'bearer ',
];

/** 令牌形态的标识符名（内存缓存会用的名字） */
const TOKEN_NAME_PATTERN = /token|bearer|credential|jwt/i;

/**
 * 属性名里**只有这几种**才算「令牌字段」。
 *
 * 刻意收窄：`credentials: 'include'`（fetch 选项）、`credential_ref`（后端字段名）、
 * `tokens`（分页用的页码令牌）都不是凭证，一律不算命中——误报会把这条断言变成噪音，
 * 最后没人再看它。
 */
const TOKEN_PROPERTY_PATTERN = /^(?:access_?token|refresh_?token|id_?token|token|bearer|jwt)$/i;

/**
 * 断言：非测试源码里不应出现令牌内存缓存。
 *
 * 改造前是 `accessToken` 这个具名变量；换名字（`cachedAuth`、`secret`）同样等于
 * 「JS 可读」。这里只允许「配置里的令牌**键名**」（`LEGACY_*_KEY`）与
 * 「会话状态四元组」这两类合法含义，其余一律上报。
 */
const ALLOWED_TOKEN_NAME =
  /^(LEGACY_SESSION_STORAGE_KEY|LEGACY_TOKEN_KEYS|SESSION_STORAGE_KEY|PENDING_STORAGE_KEY)$/;

/**
 * 判断变量声明是否处在**模块作用域**。
 *
 * 只有模块级变量才是「内存缓存」（存活到页面卸载、任何模块都能读到）。
 * 函数内部的 `const tokens = buildPageTokens(...)` 与令牌无关，不该被报。
 *
 * @param node - 变量声明节点
 * @returns 模块作用域返回 true
 */
function isModuleScopeVariable(node: ts.VariableDeclaration): boolean {
  const list = node.parent;
  if (!list || !ts.isVariableDeclarationList(list)) return false;
  const statement = list.parent;
  if (!statement || !ts.isVariableStatement(statement)) return false;
  return ts.isSourceFile(statement.parent);
}

/**
 * 扫描单个文件，返回全部发现。
 *
 * @param file - 源码文件
 * @returns 发现清单
 */
function scanFile(file: SourceFile): Finding[] {
  const isTsx = file.path.endsWith('.tsx');
  const sourceFile = ts.createSourceFile(
    file.absolutePath,
    file.text,
    ts.ScriptTarget.Latest,
    true,
    isTsx ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );

  const findings: Finding[] = [];
  const allowStorage = file.path === LEGACY_CLEANUP_FILE;

  /**
   * 记录一条发现。
   *
   * @param node - 命中节点（用于取行号）
   * @param rule - 规则标识
   */
  const report = (node: ts.Node, rule: string): void => {
    const start = node.getStart(sourceFile);
    const { line } = sourceFile.getLineAndCharacterOfPosition(start);
    findings.push({
      file: file.path,
      line: line + 1,
      rule,
      snippet: node.getText(sourceFile).replace(/\s+/g, ' ').slice(0, 80),
    });
  };

  /** 收集「拼接后的字符串常量」：`'local' + 'Storage'` 会被求值成 `localStorage` */
  const concatenated: Array<{ node: ts.Node; value: string }> = [];

  /**
   * 若节点是字符串常量（含无插值模板串），返回其文本。
   *
   * @param node - 待判定节点
   * @returns 字符串文本；否则 null
   */
  const stringTextOf = (node: ts.Node): string | null => {
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
      return node.text;
    }
    return null;
  };

  const visit = (node: ts.Node): void => {
    // ---- 1. 存储读写 -------------------------------------------------------
    if (ts.isPropertyAccessExpression(node)) {
      const target = node.name.text;
      if (target === 'localStorage' || target === 'sessionStorage') {
        if (!allowStorage) report(node, 'storage-access');
      }
      if (target === 'cookie' && ts.isIdentifier(node.expression)) {
        if (node.expression.text === 'document') report(node, 'document-cookie');
      }
      // `.setItem` / `.getItem` 出现在 session-store 里也是违规（该文件只删）
      if (target === 'setItem' || target === 'getItem') {
        report(node, 'storage-write-or-read');
      }
    }
    if (ts.isIdentifier(node)) {
      if (node.text === 'localStorage' || node.text === 'sessionStorage') {
        if (!allowStorage) report(node, 'storage-access');
      }
      // ---- 2. Cookie Store API ---------------------------------------------
      if (node.text === 'cookieStore') report(node, 'cookie-store-api');
      // ---- 3. 动态求值 -----------------------------------------------------
      if (node.text === 'eval') report(node, 'dynamic-eval');
      if (
        (node.text === 'setAccessToken' || node.text === 'getAccessToken') &&
        ts.isPropertyAccessExpression(node.parent) &&
        node.parent.name === node
      ) {
        report(node, 'token-api');
      }
    }

    if (ts.isNewExpression(node) && ts.isIdentifier(node.expression)) {
      if (node.expression.text === 'Function') report(node, 'dynamic-eval');
    }

    // ---- 1b. 计算属性访问：window['localStorage'] / globalThis['coo' + 'kie'] --
    if (ts.isElementAccessExpression(node)) {
      const argumentText = node.argumentExpression.getText(sourceFile).toLowerCase();
      if (SENSITIVE_SUBSTRINGS.some((needle) => argumentText.includes(needle.replace('.', '')))) {
        report(node, 'computed-global-access');
      }
      // 任何 window[x] / globalThis[x] / self[x] 都值得人工看一眼（不阻断，仅计数）
      if (ts.isIdentifier(node.expression)) {
        const host = node.expression.text;
        if (host === 'window' || host === 'globalThis' || host === 'self' || host === 'global') {
          report(node, 'computed-global-access');
        }
      }
    }

    // ---- 4. 令牌字面量 / 请求头字面量 ---------------------------------------
    const literal = stringTextOf(node);
    if (literal !== null) {
      const lowered = literal.toLowerCase();
      if (lowered === 'authorization') report(node, 'authorization-literal');
      if (lowered.includes('bearer ')) report(node, 'bearer-literal');
      for (const token of [
        'access_token',
        'refresh_token',
        'id_token',
        'accessToken',
        'refreshToken',
        'idToken',
      ]) {
        if (lowered === token.toLowerCase()) report(node, 'token-field-literal');
      }
    }
    // 模板串（`` `Bearer ${x}` ``）不是 StringLiteral：head / span 各自持有文本片段，
    // 漏掉它就会漏掉最常见的一种拼法
    if (ts.isTemplateExpression(node)) {
      const fragments = [node.head.text, ...node.templateSpans.map((span) => span.literal.text)];
      for (const fragment of fragments) {
        const lowered = fragment.toLowerCase();
        if (lowered.includes('bearer ')) report(node, 'bearer-literal');
        if (lowered === 'authorization') report(node, 'authorization-literal');
      }
    }

    // ---- 5. 字符串拼接混淆 --------------------------------------------------
    if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.PlusToken) {
      const left = stringTextOf(node.left);
      const right = stringTextOf(node.right);
      if (left !== null && right !== null) {
        concatenated.push({ node, value: `${left}${right}` });
      }
    }

    // ---- 5b. 从**响应头**里读令牌 -------------------------------------------
    // JS 读不到 httpOnly Cookie，但读得到 `response.headers`；若服务端把令牌
    // 塞进一个可读的响应头（`X-Access-Token`），前端一句 get 就把令牌拿回 JS 了。
    if (
      ts.isCallExpression(node) &&
      ts.isPropertyAccessExpression(node.expression) &&
      (node.expression.name.text === 'get' || node.expression.name.text === 'has')
    ) {
      const argument = node.arguments[0];
      const argumentText = argument ? argument.getText(sourceFile).toLowerCase() : '';
      if (/token|authoriz|cookie|bearer|jwt|credential/i.test(argumentText)) {
        report(node, 'token-header-read');
      }
    }

    // ---- 5c. 请求头名常量本身不能是令牌头 ------------------------------------
    if (
      ts.isVariableDeclaration(node) &&
      ts.isIdentifier(node.name) &&
      /HEADER$/.test(node.name.text) &&
      node.initializer &&
      stringTextOf(node.initializer) !== null
    ) {
      const value = (stringTextOf(node.initializer) ?? '').toLowerCase();
      if (/token|authoriz|cookie|bearer|jwt|credential/.test(value)) {
        report(node, 'token-header-constant');
      }
    }

    // ---- 6. 模块级变量名里的令牌词 -----------------------------------------
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name)) {
      const name = node.name.text;
      if (
        isModuleScopeVariable(node) &&
        TOKEN_NAME_PATTERN.test(name) &&
        !ALLOWED_TOKEN_NAME.test(name)
      ) {
        report(node, 'token-in-memory-name');
      }
    }
    if (ts.isPropertyAssignment(node)) {
      const name = node.name.getText(sourceFile).replace(/^['"]|['"]$/g, '');
      if (TOKEN_PROPERTY_PATTERN.test(name)) report(node, 'token-in-memory-name');
    }

    ts.forEachChild(node, visit);
  };

  visit(sourceFile);

  // 拼接结果也要过一遍敏感子串（`'local' + 'Storage'`）
  for (const { node, value } of concatenated) {
    const lowered = value.toLowerCase();
    if (SENSITIVE_SUBSTRINGS.some((needle) => lowered.includes(needle))) {
      report(node, 'string-split-obfuscation');
    }
  }

  return findings;
}

describe('令牌不可达 · AST 静态探针', () => {
  it('扫描面覆盖到关键文件（防止扫描器空转）', () => {
    const paths = sources().map((file) => file.path);
    for (const required of [
      'lib/session-client.ts',
      'lib/auth-refresh.ts',
      'lib/api-client.ts',
      'lib/session-store.ts',
      'lib/oidc.ts',
      'lib/session-events.ts',
    ]) {
      expect(paths).toContain(required);
    }
    // 非测试源码数量应当有几十个；只剩个位数说明目录遍历被改坏了
    expect(paths.length).toBeGreaterThan(30);
  });

  /**
   * 扫描器的**负样本自检**。
   *
   * 没有这一条，「findings 为空」可能只是扫描器坏了（恒返回空数组）。
   * 这里喂入八种「故意写坏」的源码，每一种都必须被抓到。
   */
  it('扫描器自检：八种故意写坏的样本全部被检出（否则本探针是空转的）', () => {
    const samples: Array<{ name: string; code: string; expected: string[] }> = [
      {
        name: '直接读写本地存储',
        code: "export const t = window.localStorage.getItem('access_token');",
        expected: ['storage-access', 'storage-write-or-read'],
      },
      {
        name: '计算属性访问 + 字符串拼接混淆',
        code: "const s = window['local' + 'Storage'];",
        expected: ['computed-global-access', 'string-split-obfuscation'],
      },
      {
        name: '读 Cookie',
        code: 'const raw = document.cookie;',
        expected: ['document-cookie'],
      },
      {
        name: 'Cookie Store API',
        code: "const c = await cookieStore.get('oa_session');",
        expected: ['cookie-store-api'],
      },
      {
        name: '动态求值（eval / new Function）',
        code: "const a = eval('1'); const b = new Function('return 1');",
        expected: ['dynamic-eval'],
      },
      {
        name: '注入 Authorization / Bearer',
        code: "h.set('Authorization', `Bearer ${x}`);",
        expected: ['authorization-literal', 'bearer-literal'],
      },
      {
        name: '模块级变量缓存令牌',
        code: "let accessToken = '';\nexport function set(t: string): void { accessToken = t; }",
        expected: ['token-in-memory-name'],
      },
      {
        name: '从响应头里读令牌 / 声明令牌头常量',
        code: "const TOKEN_HEADER = 'X-Access-Token';\nexport function read(r: Response): string | null { return r.headers.get('X-Access-Token'); }",
        expected: ['token-header-constant', 'token-header-read'],
      },
    ];

    for (const sample of samples) {
      const rules = new Set(
        scanFile({
          path: 'lib/evil-sample.ts',
          absolutePath: 'evil-sample.ts',
          text: sample.code,
        }).map((item) => item.rule),
      );
      for (const rule of sample.expected) {
        // 断言失败时样本名与规则名同时出现在消息里，定位不需要猜
        expect({ sample: sample.name, rule, hit: rules.has(rule) }).toEqual({
          sample: sample.name,
          rule,
          hit: true,
        });
      }
    }
  });

  it(
    '非测试源码里没有任何一条「可被脚本取到令牌」的路径',
    () => {
      const rendered = findings().map(
        (item) => `${item.file}:${item.line} [${item.rule}] ${item.snippet}`,
      );
      // 断失败时直接打印清单，便于定位（空数组时打印空行，不影响断言）
      expect(rendered).toEqual([]);
    },
    SCAN_TIMEOUT_MS,
  );

  it(
    '存储访问只出现在历史键清理文件里，且该文件只删不写',
    () => {
      const cleanup = sources().find((file) => file.path === LEGACY_CLEANUP_FILE);
      expect(cleanup).toBeDefined();
      const text = cleanup?.text ?? '';

      // 只删：removeItem 必须存在
      expect(text).toContain('removeItem');
      // 不读不写：setItem / getItem 的调用一律不存在（扫描器已按 AST 判定，
      // 这里再补一条明文断言，避免「扫描器自己被改坏」时一起失守）
      expect(/\bsetItem\s*\(/.test(text)).toBe(false);
      expect(/\bgetItem\s*\(/.test(text)).toBe(false);

      // 「只删不写」不能靠扫描器自证：明文列出该文件出现的全部存储 API，
      // 任何一项不是 removeItem 都要人工过一遍
      const storageCalls = Array.from(
        text.matchAll(/\.(setItem|getItem|removeItem|clear)\s*\(/g),
      ).map((match) => match[1]);
      expect(new Set(storageCalls)).toEqual(new Set(['removeItem']));

      // 其余文件的 AST 扫描结果里不得有任何存储 / Cookie 访问
      const othersLeaking = findings().filter(
        (item) =>
          item.file !== LEGACY_CLEANUP_FILE &&
          ['storage-access', 'document-cookie', 'storage-write-or-read'].includes(item.rule),
      );
      expect(othersLeaking).toEqual([]);
    },
    SCAN_TIMEOUT_MS,
  );
});

describe('令牌不可达 · 五端点客户端的类型面', () => {
  const clientPath = 'lib/session-client.ts';
  const client = sources().find((file) => file.path === clientPath);

  /**
   * 取出某个源文件里全部导出函数的「签名文本」。
   *
   * @param file - 源码文件
   * @returns 函数名 → 签名文本
   */
  function exportedFunctionSignatures(file: SourceFile): Map<string, string> {
    const sourceFile = ts.createSourceFile(
      file.absolutePath,
      file.text,
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TS,
    );
    const result = new Map<string, string>();
    for (const statement of sourceFile.statements) {
      if (!ts.isFunctionDeclaration(statement)) continue;
      const name = statement.name?.text;
      if (!name) continue;
      const exported = statement.modifiers?.some(
        (modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword,
      );
      if (!exported) continue;
      const returnType = statement.type?.getText(sourceFile) ?? '(inferred)';
      result.set(name, `${name}(${statement.parameters.length} args) -> ${returnType}`);
    }
    return result;
  }

  it('五个端点函数都在，且签名里没有任何令牌字段', () => {
    expect(client).toBeDefined();
    const signatures = exportedFunctionSignatures(client as SourceFile);

    for (const name of [
      'startLogin',
      'completeLogin',
      'fetchSession',
      'refreshSession',
      'logoutSession',
    ]) {
      expect(signatures.has(name)).toBe(true);
    }

    const leaking = Array.from(signatures.entries()).filter(([, signature]) =>
      /token/i.test(signature),
    );
    expect(leaking).toEqual([]);
  });

  it('SessionState / SessionGrant 两个响应类型不含令牌字段', () => {
    const text = client?.text ?? '';
    // 逐字段核对：四元组只有这四个键
    for (const field of ['authenticated', 'expires_in', 'refreshable', 'expired']) {
      expect(text).toContain(`${field}:`);
    }
    // 类型声明区里不得出现令牌字段（注释已由 AST 排除，此处只看明文关键词是否成对出现）
    const interfaceBlocks = text.match(
      /export interface (SessionState|SessionGrant) \{[\s\S]*?\n\}/g,
    );
    expect(interfaceBlocks).toHaveLength(2);
    for (const block of interfaceBlocks ?? []) {
      expect(block).not.toMatch(/access_token|refresh_token|id_token|accessToken|refreshToken/i);
    }
  });
});
