#!/usr/bin/env node
/**
 * 插件契约对齐检查（G-31）
 * ---------------------------------------------------------------------------
 * 目的：**在后端未联调时就能红**。做法是把 `contracts/phase3.pl.json`
 * （从 `docs/architecture-phase3.md` §5.2/§5.3 抄下来的契约快照）与
 * `src/lib/types.ts` 里的接口逐键比对：
 *
 *   - 缺键（契约有、类型没有）→ 红
 *   - 多键（类型有、契约没有）→ 红
 *   - 可选性翻转（契约必填、类型写成可选，或反之）→ 红
 *   - 接口缺失 → 红
 *
 * 为什么不用 vitest：这件事本质是「类型声明与契约文本对齐」，
 * 一个零依赖静态脚本即可，且不引入新的 CI 安装与版本维护成本。
 *
 * 用法（在 apps/web 下执行）：
 *   node scripts/check-plugin-contract.mjs              # 比对
 *   node scripts/check-plugin-contract.mjs --json
 *   node scripts/check-plugin-contract.mjs --self-test   # 负样本自检
 *
 * 退出码（与既有两个扫描器一致，**故意如此**）：
 *   0 = 完全一致
 *   1 = 有差异；`--self-test` 下 1 表示**自检通过**（负样本全部被抓到）
 *   2 = 参数/文件错误（快照或 types.ts 读不到）
 */

import { readFileSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = resolve(HERE, '..');
const TYPES_PATH = join(WEB_ROOT, 'src/lib/types.ts');
const CONTRACT_PATH = join(WEB_ROOT, 'contracts/phase3.pl.json');

/**
 * 从 TypeScript 源码里解析 `export interface X { ... }` 的顶层字段。
 *
 * 解析口径：
 * - 只认**顶格两空格缩进**的字段行（本仓库 types.ts 的书写约定），
 *   这样嵌套对象字面量里的字段不会被误当成顶层字段；
 * - 先剔除块注释与行注释，避免注释里的冒号干扰；
 * - `字段?:` 记为 optional，`字段:` 记为 required。
 *
 * @param {string} source - types.ts 源码
 * @returns {Map<string, {required: string[], optional: string[]}>} 接口名 → 字段集合
 */
export function parseInterfaces(source) {
  const cleaned = source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1');

  const result = new Map();
  const headerPattern = /export interface ([A-Za-z0-9_]+)[^{]*\{/g;
  let match;

  while ((match = headerPattern.exec(cleaned)) !== null) {
    const name = match[1];
    let index = match.index + match[0].length;
    let depth = 1;
    let body = '';
    while (index < cleaned.length && depth > 0) {
      const ch = cleaned[index];
      if (ch === '{') depth += 1;
      else if (ch === '}') {
        depth -= 1;
        if (depth === 0) break;
      }
      body += ch;
      index += 1;
    }

    const required = [];
    const optional = [];
    for (const line of body.split('\n')) {
      const field = /^ {2}([A-Za-z_][A-Za-z0-9_]*)(\?)?\s*:/.exec(line);
      if (!field) continue;
      if (field[2] === '?') optional.push(field[1]);
      else required.push(field[1]);
    }
    result.set(name, { required, optional });
  }

  return result;
}

/**
 * 比对单个接口。
 *
 * @param {string} name - 接口名
 * @param {{required: string[], optional: string[]}} expected - 契约快照
 * @param {{required: string[], optional: string[]}|undefined} actual - 类型声明
 * @returns {Array<string>} 差异说明（空数组表示一致）
 */
export function compareInterface(name, expected, actual) {
  if (!actual) {
    return [`缺少接口 ${name}`];
  }

  const problems = [];
  const expectedRequired = new Set(expected.required ?? []);
  const expectedOptional = new Set(expected.optional ?? []);
  const actualRequired = new Set(actual.required);
  const actualOptional = new Set(actual.optional);

  for (const key of expectedRequired) {
    if (actualOptional.has(key)) {
      problems.push(`${name}.${key} 可选性翻转：契约必填，类型写成可选`);
    } else if (!actualRequired.has(key)) {
      problems.push(`${name}.${key} 缺键：契约必填，类型中不存在`);
    }
  }

  for (const key of expectedOptional) {
    if (actualRequired.has(key)) {
      problems.push(`${name}.${key} 可选性翻转：契约可选，类型写成必填`);
    } else if (!actualOptional.has(key)) {
      problems.push(`${name}.${key} 缺键：契约可选，类型中不存在`);
    }
  }

  for (const key of actualRequired) {
    if (!expectedRequired.has(key) && !expectedOptional.has(key)) {
      problems.push(`${name}.${key} 多键：类型中存在，契约未声明`);
    }
  }

  for (const key of actualOptional) {
    if (!expectedRequired.has(key) && !expectedOptional.has(key)) {
      problems.push(`${name}.${key} 多键：类型中存在（可选），契约未声明`);
    }
  }

  return problems;
}

/**
 * 比对整个快照。
 *
 * @param {{interfaces: Record<string, {required: string[], optional: string[]}>}} contract - 契约快照
 * @param {Map<string, {required: string[], optional: string[]}>} interfaces - 解析出的接口表
 * @returns {{problems: Array<string>, interfaceCount: number, fieldCount: number}} 比对结果
 */
export function compareAll(contract, interfaces) {
  const problems = [];
  let interfaceCount = 0;
  let fieldCount = 0;

  for (const [name, expected] of Object.entries(contract.interfaces ?? {})) {
    interfaceCount += 1;
    fieldCount += (expected.required?.length ?? 0) + (expected.optional?.length ?? 0);
    problems.push(...compareInterface(name, expected, interfaces.get(name)));
  }

  return { problems, interfaceCount, fieldCount };
}

// ---------------------------------------------------------------------------
// 自检：负样本必须被抓到
// ---------------------------------------------------------------------------

/** 合成样本：与契约完全一致 */
const BASE_SOURCE = `
export interface DemoItem {
  plugin_id: string;
  name: string;
  has_update?: boolean;
}
`;

/** 合成契约 */
const BASE_CONTRACT = {
  interfaces: {
    DemoItem: { required: ['plugin_id', 'name'], optional: ['has_update'] },
  },
};

/** 负样本：各自只破坏一件事 */
const NEGATIVE = [
  {
    name: '缺必填键',
    expect: '缺键',
    source: `export interface DemoItem {\n  plugin_id: string;\n}\n`,
  },
  {
    name: '多出未声明键',
    expect: '多键',
    source: `export interface DemoItem {\n  plugin_id: string;\n  name: string;\n  extra_field: string;\n}\n`,
  },
  {
    name: '必填写成可选',
    expect: '可选性翻转',
    source: `export interface DemoItem {\n  plugin_id?: string;\n  name: string;\n}\n`,
  },
  {
    name: '可选写成必填',
    expect: '可选性翻转',
    source: `export interface DemoItem {\n  plugin_id: string;\n  name: string;\n  has_update: boolean;\n}\n`,
  },
  {
    name: '接口整体缺失',
    expect: '缺少接口',
    source: `export interface OtherThing {\n  plugin_id: string;\n}\n`,
  },
];

/**
 * 自检。
 *
 * @returns {number} 0 失败 / 1 通过（故意：通过=1，让门禁「失效即红」）
 */
function selfTest() {
  const failures = [];

  const base = compareAll(BASE_CONTRACT, parseInterfaces(BASE_SOURCE));
  if (base.problems.length > 0) {
    failures.push(`正样本误报：${base.problems.join('；')}`);
  }

  // 解析器本身要能吃到嵌套字段之外的顶层字段（嵌套不应被误算）
  const nested = parseInterfaces(
    'export interface Nested {\n  outer: { inner_a: string };\n  sibling: number;\n}\n',
  );
  const nestedFields = nested.get('Nested');
  if (!nestedFields || nestedFields.required.join(',') !== 'outer,sibling') {
    failures.push(`嵌套对象字段被误解析：${JSON.stringify(nestedFields)}`);
  }

  for (const sample of NEGATIVE) {
    const result = compareAll(BASE_CONTRACT, parseInterfaces(sample.source));
    if (!result.problems.some((problem) => problem.includes(sample.expect))) {
      failures.push(
        `负样本漏抓「${sample.name}」：期望命中「${sample.expect}」，实际 ${result.problems.join('；') || '无问题'}`,
      );
    }
  }

  if (failures.length > 0) {
    console.error(`SELF-TEST FAIL（${failures.length}）`);
    for (const failure of failures) console.error('  -', failure);
    return 0;
  }

  console.info(
    `SELF-TEST PASS：正样本 1 条 0 误报；嵌套字段解析正确；` +
      `负样本 ${NEGATIVE.length}/${NEGATIVE.length} 全部抓到（缺键/多键/双向可选性翻转/接口缺失）`,
  );
  return 1;
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

function main(argv) {
  if (argv.includes('--self-test')) return selfTest();

  let source;
  let contract;
  try {
    source = readFileSync(TYPES_PATH, 'utf8');
    contract = JSON.parse(readFileSync(CONTRACT_PATH, 'utf8'));
  } catch (error) {
    console.error('[plugin-contract] 读取失败：', error instanceof Error ? error.message : error);
    return 2;
  }

  const interfaces = parseInterfaces(source);
  const { problems, interfaceCount, fieldCount } = compareAll(contract, interfaces);

  if (argv.includes('--json')) {
    console.info(JSON.stringify({ interfaceCount, fieldCount, problems }, null, 2));
  } else if (problems.length === 0) {
    console.info(
      `检查 ${interfaceCount} 个接口 / ${fieldCount} 个字段，与契约快照完全一致（0 差异）` +
        `（快照 ${relative(WEB_ROOT, CONTRACT_PATH).replace(/\\/g, '/')}）`,
    );
  } else {
    console.info(`检查 ${interfaceCount} 个接口 / ${fieldCount} 个字段，${problems.length} 处差异：`);
    for (const problem of problems) console.info(`  - ${problem}`);
  }

  return problems.length > 0 ? 1 : 0;
}

process.exit(main(process.argv.slice(2)));
