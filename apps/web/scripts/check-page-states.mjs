#!/usr/bin/env node
/**
 * 页面三态门禁（G-18）
 * ---------------------------------------------------------------------------
 * 依据：`docs/prd-phase2-increment.md` + 阶段门「每个业务页面必须有加载 / 空 / 错误三态，
 * 且错误态必须可重试」。这是最容易在赶工中被悄悄删掉的一类体验兜底，
 * 人工目测靠不住，因此做成静态断言。
 *
 * 用法（在 apps/web 下执行）：
 *   node scripts/check-page-states.mjs              # 检查 10 个业务页
 *   node scripts/check-page-states.mjs --json
 *   node scripts/check-page-states.mjs --self-test  # 负样本自检（通过时返回 1）
 *
 * 退出码（与禁词 / 占位词扫描器保持同一套约定）：
 *   0 = 全部满足
 *   1 = 有缺失；`--self-test` 下 1 表示**自检通过**（负样本全部被抓到）
 *   2 = 参数/文件错误
 *
 * 判定口径：
 * 1. **错误态最严**：页面自身必须渲染 `ErrorState`，且**必须传 `onRetry`**（不放宽、不代理）；
 * 2. 加载态 / 空态允许两种等价格式：
 *    a. 直接在页面里用 `LoadingState` / `Skeleton` / `TableLoading`、`EmptyState` / `TableEmpty`；
 *    b. 由页面**直接引用的本地子组件**提供（例如表格页把空态放进 `<td colspan>` 的 `TableEmpty`，
 *       详情页把文档区整块交给 `DocumentTable`）。这样既符合真实结构，也不会诱导
 *       后来者为了过门禁在表格外面再套一层假空态。
 *    代理只扫描 **一层**（页面直接 import 的组件），不做全树递归，避免误判与性能问题。
 */

import { readFileSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = resolve(HERE, '..');
const SRC_ROOT = join(WEB_ROOT, 'src');

/** 受门禁约束的 10 个业务页面（路由 → 源码路径，均相对 src/app） */
export const PAGES = [
  { route: '/chat', file: '(main)/chat/page.tsx' },
  { route: '/knowledge', file: '(main)/knowledge/page.tsx' },
  { route: '/knowledge/[baseId]', file: '(main)/knowledge/[baseId]/page.tsx' },
  { route: '/plugins', file: '(main)/plugins/page.tsx' },
  { route: '/workflows', file: '(main)/workflows/page.tsx' },
  { route: '/team', file: '(main)/team/page.tsx' },
  { route: '/team/organization', file: '(main)/team/organization/page.tsx' },
  { route: '/profile', file: '(main)/profile/page.tsx' },
  { route: '/billing', file: '(main)/billing/page.tsx' },
  { route: '/ops/tenants', file: '(main)/ops/tenants/page.tsx' },
];

/** 装载态可接受的符号（含表格/骨架别名） */
const LOADING_SYMBOLS = ['LoadingState', 'TableLoading', 'Skeleton'];

/** 空态可接受的符号（含表格行内别名） */
const EMPTY_SYMBOLS = ['EmptyState', 'TableEmpty'];

/** 错误态唯一符号 */
const ERROR_SYMBOL = 'ErrorState';

/**
 * 判断文本里是否使用了某个符号。
 *
 * @param {string} source - 源码
 * @param {string} symbol - 符号名
 * @returns {boolean} 出现返回 true
 */
function usesSymbol(source, symbol) {
  return new RegExp(`\\b${symbol}\\b`).test(source);
}

/**
 * 找出所有 `<ErrorState ...>` 开始标签的属性区间。
 *
 * 逐字符扫描并跟踪花括号深度，避免被 `onRetry={() => doIt()}` 里的 `>` 提前截断。
 *
 * @param {string} source - 源码
 * @returns {Array<string>} 每个开始标签的属性文本
 */
export function extractErrorStateTags(source) {
  const tags = [];
  const needle = `<${ERROR_SYMBOL}`;
  let cursor = 0;

  for (;;) {
    const start = source.indexOf(needle, cursor);
    if (start === -1) break;

    let index = start + needle.length;
    let depth = 0;
    let tag = '';
    while (index < source.length) {
      const ch = source[index];
      if (ch === '{') depth += 1;
      else if (ch === '}') depth = Math.max(0, depth - 1);
      else if (ch === '>' && depth === 0) break;
      else if (ch === '/' && source[index + 1] === '>') break;
      tag += ch;
      index += 1;
    }
    tags.push(tag);
    cursor = index + 1;
  }

  return tags;
}

/**
 * 检查页面源码是否满足三态要求。
 *
 * @param {string} source - 页面源码
 * @param {Array<string>} helpers - 页面直接引用子组件的源码（用于判断三态是否由子组件提供）
 * @returns {Array<string>} 问题列表（空数组表示通过）
 */
export function checkSource(source, helpers = []) {
  const problems = [];
  const all = [source, ...helpers];

  const hasLoading = all.some((text) => LOADING_SYMBOLS.some((symbol) => usesSymbol(text, symbol)));
  const hasEmpty = all.some((text) => EMPTY_SYMBOLS.some((symbol) => usesSymbol(text, symbol)));
  const hasError = usesSymbol(source, ERROR_SYMBOL);

  if (!hasLoading) problems.push(`缺少加载态（${LOADING_SYMBOLS.join(' / ')}）`);
  if (!hasEmpty) problems.push(`缺少空态（${EMPTY_SYMBOLS.join(' / ')}，页面或其直接子组件均可）`);
  if (!hasError) {
    problems.push(`缺少错误态（${ERROR_SYMBOL}，必须由页面自身渲染）`);
  } else {
    const tags = extractErrorStateTags(source);
    if (tags.length === 0) {
      problems.push(`引用了 ${ERROR_SYMBOL} 但没有实际渲染`);
    }
    const missingRetry = tags.filter((tag) => !/\bonRetry\s*=/.test(tag));
    if (missingRetry.length > 0) {
      problems.push(`${ERROR_SYMBOL} 有 ${missingRetry.length} 处未传 onRetry（错误态必须可重试）`);
    }
  }

  return problems;
}

/**
 * 抽取源码里引用的本地模块说明符（`@/...` 与 `./`、`import('...')`）。
 *
 * @param {string} source - 源码
 * @returns {Array<string>} 说明符列表
 */
export function collectLocalImportSpecs(source) {
  const specs = new Set();
  for (const match of source.matchAll(/from\s+['"]([^'"]+)['"]/g)) {
    const spec = match[1];
    if (spec.startsWith('@/') || spec.startsWith('./') || spec.startsWith('../')) {
      specs.add(spec);
    }
  }
  for (const match of source.matchAll(/import\(\s*['"]([^'"]+)['"]\s*\)/g)) {
    const spec = match[1];
    if (spec.startsWith('@/') || spec.startsWith('./') || spec.startsWith('../')) {
      specs.add(spec);
    }
  }
  return [...specs];
}

/**
 * 把模块说明符解析成磁盘文件（仅支持 ts/tsx 与目录 index）。
 *
 * @param {string} spec - 模块说明符
 * @param {string} fromFile - 引用方文件绝对路径
 * @returns {string | null} 解析到的文件路径；解析不到返回 null
 */
function resolveSpecToFile(spec, fromFile) {
  const base = spec.startsWith('@/') ? join(SRC_ROOT, spec.slice(2)) : resolve(dirname(fromFile), spec);
  const candidates = [`${base}.tsx`, `${base}.ts`, join(base, 'index.tsx'), join(base, 'index.ts')];
  for (const candidate of candidates) {
    try {
      readFileSync(candidate, 'utf8');
      return candidate;
    } catch {
      // 继续尝试下一个候选
    }
  }
  return null;
}

/**
 * 读取页面 + 其直接引用的本地组件源码。
 *
 * @param {string} absolute - 页面绝对路径
 * @returns {{source: string, helpers: Array<string>, helperFiles: Array<string>}}
 */
function loadWithHelpers(absolute) {
  const source = readFileSync(absolute, 'utf8');
  const helpers = [];
  const helperFiles = [];
  for (const spec of collectLocalImportSpecs(source)) {
    const file = resolveSpecToFile(spec, absolute);
    if (!file) continue;
    try {
      helpers.push(readFileSync(file, 'utf8'));
      helperFiles.push(relative(WEB_ROOT, file).replace(/\\/g, '/'));
    } catch {
      // 读不到就跳过：不作为失败依据
    }
  }
  return { source, helpers, helperFiles };
}

/**
 * 读取并检查一个页面文件。
 *
 * @param {{route: string, file: string}} page - 页面定义
 * @returns {{route: string, file: string, problems: Array<string>, helpers: Array<string>}} 检查结果
 */
function checkPage(page) {
  const absolute = join(SRC_ROOT, 'app', page.file);
  const shown = relative(WEB_ROOT, absolute).replace(/\\/g, '/');
  try {
    const { source, helpers, helperFiles } = loadWithHelpers(absolute);
    return { route: page.route, file: shown, problems: checkSource(source, helpers), helpers: helperFiles };
  } catch {
    return { route: page.route, file: shown, problems: ['页面文件不存在或无法读取'], helpers: [] };
  }
}

// ---------------------------------------------------------------------------
// 自检：正样本不得误报，负样本必须被抓到
// ---------------------------------------------------------------------------

/** 正样本（合法写法，必须通过） */
const POSITIVE = [
  {
    name: '三个组件齐全 + 错误态带 onRetry',
    source: `
      import { EmptyState, ErrorState, LoadingState } from '@/components/ui/feedback';
      export default function Page() {
        return <ErrorState message={msg} onRetry={() => void query.refetch()} />;
      }
    `,
  },
  {
    name: '表格别名写法（TableLoading / TableEmpty）',
    source: `
      import { ErrorState } from '@/components/ui/feedback';
      <TableBody>
        <TableLoading columns={6} />
        <TableEmpty colSpan={6} title="没有数据" />
        <ErrorState message={msg} onRetry={retry} />
      </TableBody>
    `,
  },
  {
    name: 'onRetry 内联箭头函数（含 > 字符，考验标签扫描）',
    source: `
      <ErrorState
        message={describe(error)}
        onRetry={() => setFilter((prev) => ({ ...prev, page: 1 }))}
      />
      <LoadingState />
      <EmptyState title="还没有数据" />
    `,
  },
  {
    name: '空态由直接子组件提供',
    source: `
      import { DocumentTable } from '@/components/knowledge/document-table';
      <LoadingState />
      <ErrorState message={msg} onRetry={retry} />
      <DocumentTable items={items} />
    `,
    helpers: [`<TableEmpty colSpan={8} title="还没有上传文档" />`],
  },
];

/** 负样本（必须被抓到） */
const NEGATIVE = [
  { name: '缺空态', expect: '缺少空态', source: `<LoadingState /><ErrorState message={m} onRetry={r} />` },
  { name: '缺加载态', expect: '缺少加载态', source: `<EmptyState title="x" /><ErrorState message={m} onRetry={r} />` },
  { name: '缺错误态', expect: '缺少错误态', source: `<LoadingState /><EmptyState title="x" />` },
  {
    name: '错误态没传 onRetry',
    expect: '未传 onRetry',
    source: `<LoadingState /><EmptyState title="x" /><ErrorState message={m} />`,
  },
  {
    name: '只在注释里提到三个组件',
    expect: '没有实际渲染',
    source: `// LoadingState EmptyState ErrorState 后面会加\nexport default function Page() { return null; }`,
  },
  {
    name: '空态想靠子组件带，但子组件也没有',
    expect: '缺少空态',
    source: `import { Foo } from '@/components/foo';\n<LoadingState /><ErrorState message={m} onRetry={r} /><Foo />`,
    helpers: [`export function Foo() { return <div>暂无</div>; }`],
  },
];

/**
 * 自检。
 *
 * @returns {number} 0 失败 / 1 通过（故意：通过=1，让门禁「失效即红」）
 */
function selfTest() {
  const failures = [];

  for (const sample of POSITIVE) {
    const problems = checkSource(sample.source, sample.helpers ?? []);
    if (problems.length > 0) {
      failures.push(`正样本误报「${sample.name}」：${problems.join('；')}`);
    }
  }

  for (const sample of NEGATIVE) {
    const problems = checkSource(sample.source, sample.helpers ?? []);
    const caught = problems.some((problem) => problem.includes(sample.expect));
    if (!caught) {
      failures.push(
        `负样本漏抓「${sample.name}」：期望命中「${sample.expect}」，实际 ${problems.join('；') || '无问题'}`,
      );
    }
  }

  if (failures.length > 0) {
    console.error(`SELF-TEST FAIL（${failures.length}）`);
    for (const failure of failures) console.error('  -', failure);
    return 0;
  }

  console.info(
    `SELF-TEST PASS：正样本 ${POSITIVE.length} 条 0 误报（含子组件代理写法）；` +
      `负样本 ${NEGATIVE.length}/${NEGATIVE.length} 全部抓到`,
  );
  return 1;
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

function main(argv) {
  if (argv.includes('--self-test')) return selfTest();

  const json = argv.includes('--json');
  const results = PAGES.map(checkPage);
  const failed = results.filter((item) => item.problems.length > 0);

  if (json) {
    console.info(JSON.stringify(results, null, 2));
  } else if (failed.length === 0) {
    console.info(`检查 ${results.length} 个业务页，三态齐备且错误态可重试（0 问题）`);
    for (const item of results) {
      console.info(`  ✓ ${item.route}  ← ${item.file}（子组件 ${item.helpers.length} 个参与判定）`);
    }
  } else {
    console.info(`检查 ${results.length} 个业务页，${failed.length} 个不满足：`);
    for (const item of failed) {
      console.info(`  ✗ ${item.route}  ← ${item.file}`);
      for (const problem of item.problems) console.info(`      - ${problem}`);
    }
  }

  return failed.length > 0 ? 1 : 0;
}

process.exit(main(process.argv.slice(2)));
