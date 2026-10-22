#!/usr/bin/env node
/**
 * 工作台宿主门禁（G-27）
 * ---------------------------------------------------------------------------
 * 三条红线，全部做成静态断言（PRD P0-P3-24 / 架构 D-17）：
 *
 * 1. **单宿主**：`app/(main)/workbench` 下只允许一个 `page.tsx`，且必须是 `[pluginId]` 动态段；
 *    出现 `workbench/plg_xxx/page.tsx` 这类「一插件一页面」即红。
 * 2. **零按插件分支**：`src/**` 里不允许出现 `pluginId ===` / `pluginId ==` / `pluginId !==`，
 *    也不允许出现写死的插件标识字面量（`'plg_...'`）——否则「6 插件共用一宿主」名存实亡。
 * 3. **零自有样式与三方 UI**：插件相关目录 0 个 `.css` / 0 个 `.jsx`；
 *    这些文件的 import 只能来自平台组件库与框架白名单（禁止 `@radix-ui/*` 直引、禁止第三方 UI 库）。
 *
 * 用法（在 apps/web 下执行）：
 *   node scripts/check-workbench-host.mjs
 *   node scripts/check-workbench-host.mjs --json
 *   node scripts/check-workbench-host.mjs --self-test   # 自检通过返回 1（同既有扫描器约定）
 *
 * 退出码：0 = 全部满足；1 = 有违规（`--self-test` 下 1 表示自检通过）；2 = 路径错误
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = resolve(HERE, '..');
const SRC_ROOT = join(WEB_ROOT, 'src');

/** 工作台宿主目录 */
const WORKBENCH_DIR = join(SRC_ROOT, 'app/(main)/workbench');

/** 受「零样式 / 零三方 UI」约束的目录（插件与工作台的全部前端资产） */
const PLUGIN_DIRS = [
  'components/plugin',
  'components/workbench',
  'app/(main)/plugins',
  'app/(main)/workbench',
];

/** import 白名单：只允许平台组件库、框架与自身业务目录 */
const ALLOWED_IMPORT_PREFIXES = [
  'react',
  'react-dom',
  'next/',
  'lucide-react',
  '@/components/ui/',
  '@/components/common/',
  '@/components/plugin/',
  '@/components/workbench/',
  '@/lib/',
  '@/hooks/',
];

/** 明确禁止的 UI 依赖（出现即红，即使将来装了也不许用） */
const FORBIDDEN_IMPORT_PREFIXES = ['@radix-ui/', 'antd', '@mui/', '@chakra-ui/', 'styled-components', '@emotion/'];

/** 按插件分支的特征（允许 `pluginId` 传参、拼路径，但不允许比较它） */
const PLUGIN_BRANCH_PATTERNS = [
  { pattern: /pluginId\s*===/, hint: 'pluginId ===' },
  { pattern: /pluginId\s*==(?!=)/, hint: 'pluginId ==' },
  { pattern: /pluginId\s*!==/, hint: 'pluginId !==' },
  { pattern: /switch\s*\(\s*pluginId/, hint: 'switch (pluginId)' },
  // layoutMap[pluginId] 这类「用插件 id 当查表键」的写法同样是按插件分叉
  { pattern: /\[\s*pluginId\s*\]/, hint: 'layoutMap[pluginId]' },
];

/** 写死插件标识（`plg_xxx`）的特征 */
const HARDCODED_PLUGIN_ID = /['"`]plg_[a-z0-9_]+['"`]/;

/** 递归收集文件 */
function walk(dir, acc = []) {
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return acc;
  }
  for (const name of entries) {
    const full = join(dir, name);
    let info;
    try {
      info = statSync(full);
    } catch {
      continue;
    }
    if (info.isDirectory()) walk(full, acc);
    else acc.push(full);
  }
  return acc;
}

/**
 * 收集工作台下所有 page 文件（相对 workbench 目录）。
 *
 * @param {string} workbenchDir - 工作台目录
 * @returns {Array<string>} 相对路径
 */
export function collectWorkbenchPages(workbenchDir) {
  return walk(workbenchDir)
    .filter((file) => file.endsWith('page.tsx'))
    .map((file) => relative(workbenchDir, file).replace(/\\/g, '/'));
}

/**
 * 校验「单宿主」。
 *
 * @param {Array<string>} pages - 相对路径列表
 * @returns {Array<string>} 问题
 */
export function checkSingleHost(pages) {
  const problems = [];
  if (pages.length === 0) {
    problems.push('没有找到工作台宿主页面（app/(main)/workbench/.../page.tsx）');
    return problems;
  }
  if (pages.length > 1) {
    problems.push(`工作台宿主不唯一：发现 ${pages.length} 个 page.tsx（${pages.join('、')}）`);
  }
  if (!pages.includes('[pluginId]/page.tsx')) {
    problems.push('宿主必须位于 [pluginId] 动态段下（否则无法承载 6 个插件）');
  }
  return problems;
}

/**
 * 去掉注释（注释里写「没有 if (pluginId === ...)」不应被判违规）。
 *
 * @param {string} content - 源码
 * @returns {string} 去掉注释后的源码
 */
export function stripComments(content) {
  return content
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

/**
 * 校验源码里没有「按插件分支 / 写死插件标识」。
 *
 * @param {{file: string, content: string}} sample - 文件内容
 * @returns {Array<string>} 问题
 */
export function checkNoPluginBranch(sample) {
  const problems = [];
  const content = stripComments(sample.content);
  for (const { pattern, hint } of PLUGIN_BRANCH_PATTERNS) {
    if (pattern.test(content)) {
      problems.push(`${sample.file} 出现按插件分支「${hint}」（宿主必须与插件无关）`);
    }
  }
  if (HARDCODED_PLUGIN_ID.test(content)) {
    problems.push(`${sample.file} 出现写死的插件标识（plg_*），应由声明/接口驱动`);
  }
  return problems;
}

/**
 * 抽取 import 说明符。
 *
 * @param {string} content - 源码
 * @returns {Array<string>} 说明符
 */
export function collectImports(content) {
  const specs = [];
  for (const match of content.matchAll(/from\s+['"]([^'"]+)['"]/g)) specs.push(match[1]);
  for (const match of content.matchAll(/import\(\s*['"]([^'"]+)['"]\s*\)/g)) specs.push(match[1]);
  return specs;
}

/**
 * 校验 import 白名单。
 *
 * @param {{file: string, content: string}} sample - 文件内容
 * @returns {Array<string>} 问题
 */
export function checkImports(sample) {
  const problems = [];
  for (const spec of collectImports(sample.content)) {
    if (FORBIDDEN_IMPORT_PREFIXES.some((prefix) => spec === prefix || spec.startsWith(prefix))) {
      problems.push(`${sample.file} 引用了禁止依赖「${spec}」（只能用平台组件库）`);
      continue;
    }
    if (ALLOWED_IMPORT_PREFIXES.some((prefix) => spec === prefix || spec.startsWith(prefix))) {
      continue;
    }
    problems.push(`${sample.file} 引用了白名单外的模块「${spec}」`);
  }
  return problems;
}

/**
 * 校验目录里没有自有样式 / jsx。
 *
 * @param {Array<string>} files - 文件绝对路径列表
 * @returns {Array<string>} 问题
 */
export function checkNoOwnAssets(files) {
  const problems = [];
  for (const file of files) {
    if (file.endsWith('.css') || file.endsWith('.scss') || file.endsWith('.jsx')) {
      problems.push(`插件目录出现自有样式/组件文件：${file}`);
    }
  }
  return problems;
}

// ---------------------------------------------------------------------------
// 自检
// ---------------------------------------------------------------------------

const NEGATIVE = [
  {
    name: '两个宿主页面（一插件一页面）',
    expect: '宿主不唯一',
    run: () => checkSingleHost(['[pluginId]/page.tsx', 'plg_dev_workbench/page.tsx']),
  },
  {
    name: '宿主不在动态段下',
    expect: '动态段',
    run: () => checkSingleHost(['page.tsx']),
  },
  {
    name: '按 pluginId 分支',
    expect: '按插件分支',
    run: () => checkNoPluginBranch({
      file: 'page.tsx',
      content: "if (pluginId === 'plg_dev_workbench') { return <DevLayout />; }",
    }),
  },
  {
    name: 'switch(pluginId) 分叉',
    expect: 'switch (pluginId)',
    run: () => checkNoPluginBranch({
      file: 'page.tsx',
      content: 'switch (pluginId) { default: break; }',
    }),
  },
  {
    name: 'layoutMap[pluginId] 查表分叉',
    expect: 'layoutMap[pluginId]',
    run: () => checkNoPluginBranch({
      file: 'page.tsx',
      content: 'const Layout = layoutMap[pluginId];',
    }),
  },
  {
    name: '写死插件标识',
    expect: '写死的插件标识',
    run: () => checkNoPluginBranch({ file: 'page.tsx', content: "const id = 'plg_hr_admin';" }),
  },
  {
    name: '自有样式文件',
    expect: '自有样式',
    run: () => checkNoOwnAssets(['src/components/plugin/plugin-card.css']),
  },
  {
    name: '引用第三方 UI 库',
    expect: '禁止依赖',
    run: () => checkImports({
      file: 'plugin-card.tsx',
      content: "import { Dialog } from '@radix-ui/react-dialog';",
    }),
  },
  {
    name: '引用白名单外业务域',
    expect: '白名单外',
    run: () => checkImports({
      file: 'workbench.tsx',
      content: "import { GrantPanel } from '@/components/knowledge/grant-panel';",
    }),
  },
];

const POSITIVE = [
  () => checkSingleHost(['[pluginId]/page.tsx']),
  () =>
    checkNoPluginBranch({
      file: 'page.tsx',
      content: "const href = `/workbench/${pluginId}`; export default function Page() { return null; }",
    }),
  () =>
    checkImports({
      file: 'page.tsx',
      content:
        "import * as React from 'react';\nimport { Card } from '@/components/ui/card';\nimport { DeclarationForm } from '@/components/workbench/declaration-form';\nimport { pluginsApi } from '@/lib/api-client';",
    }),
  () => checkNoOwnAssets(['src/components/plugin/plugin-card.tsx']),
  () =>
    checkNoPluginBranch({
      file: 'page.tsx',
      content: '// 这里没有 if (pluginId === ...) 分支\nexport const a = 1;',
    }),
];

/**
 * 自检。
 *
 * @returns {number} 0 失败 / 1 通过（故意：通过=1）
 */
function selfTest() {
  const failures = [];

  for (const item of POSITIVE) {
    const problems = item();
    if (problems.length > 0) failures.push(`正样本误报：${problems.join('；')}`);
  }

  for (const sample of NEGATIVE) {
    const problems = sample.run();
    if (!problems.some((problem) => problem.includes(sample.expect))) {
      failures.push(
        `负样本漏抓「${sample.name}」：期望含「${sample.expect}」，实际 ${problems.join('；') || '无问题'}`,
      );
    }
  }

  if (failures.length > 0) {
    console.error(`SELF-TEST FAIL（${failures.length}）`);
    for (const failure of failures) console.error('  -', failure);
    return 0;
  }

  console.info(
    `SELF-TEST PASS：正样本 ${POSITIVE.length} 条 0 误报（含注释内提及）；` +
      `负样本 ${NEGATIVE.length}/${NEGATIVE.length} 全部抓到（多宿主/非动态段/比较/switch/查表/写死标识/自有样式/三方 UI/跨域引用）`,
  );
  return 1;
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

function main(argv) {
  if (argv.includes('--self-test')) return selfTest();

  const pages = collectWorkbenchPages(WORKBENCH_DIR);
  const problems = [...checkSingleHost(pages)];

  let branchScanned = 0;
  for (const file of walk(SRC_ROOT)) {
    if (!/\.(ts|tsx)$/.test(file) || file.endsWith('.d.ts')) continue;
    branchScanned += 1;
    const content = readFileSync(file, 'utf8');
    problems.push(...checkNoPluginBranch({ file: relative(WEB_ROOT, file).replace(/\\/g, '/'), content }));
  }

  let assetScanned = 0;
  for (const dir of PLUGIN_DIRS) {
    const files = walk(join(SRC_ROOT, dir));
    assetScanned += files.length;
    problems.push(...checkNoOwnAssets(files.map((file) => relative(WEB_ROOT, file).replace(/\\/g, '/'))));
    for (const file of files) {
      if (!/\.(ts|tsx)$/.test(file)) continue;
      problems.push(
        ...checkImports({
          file: relative(WEB_ROOT, file).replace(/\\/g, '/'),
          content: readFileSync(file, 'utf8'),
        }),
      );
    }
  }

  if (argv.includes('--json')) {
    console.info(JSON.stringify({ pages, branchScanned, assetScanned, problems }, null, 2));
  } else if (problems.length === 0) {
    console.info(
      `G-27 三项红线全绿：宿主 ${pages.length} 个（${pages.join('、')}）；` +
        `分支扫描 ${branchScanned} 个源文件 0 命中；插件目录 ${assetScanned} 个文件 0 个 .css/.jsx、import 全部在白名单内`,
    );
  } else {
    console.info(`G-27 发现 ${problems.length} 个问题：`);
    for (const problem of problems) console.info(`  - ${problem}`);
  }

  return problems.length > 0 ? 1 : 0;
}

process.exit(main(process.argv.slice(2)));
