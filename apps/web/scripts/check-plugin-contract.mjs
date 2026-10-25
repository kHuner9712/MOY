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
 *   - **枚举缺值 / 多值 → 红**（`enums` 段：TaskStatus / StepStatus / ArtifactType /
 *     PluginInstalledStatus / PluginCapabilityKey）。枚举漂移是这类契约最常出的错，
 *     只校验键名抓不到（例如 `queued` 漏了、`completed`/`succeeded` 混用）。
 *
 * 为什么不用 vitest：这件事本质是「类型声明与契约文本对齐」，
 * 一个零依赖静态脚本即可，且不引入新的 CI 安装与版本维护成本。
 *
 * **两个模式（W-01 契约换源后新增 `--generated`）**
 *
 * | 模式 | 真相源 | 比什么 | 谁负责可选性 |
 * | --- | --- | --- | --- |
 * | 默认（快照） | `contracts/phase3.pl.json`（手写） | 键集合 **+ 可选性 + 枚举** | 快照 |
 * | `--generated` | `contracts/plugin-domain.gen.json`（由 `deploy/export_plugin_contract.py` 从 `create_app().openapi()` 派生） | **只比键集合**（双向严格） | 仍由快照 ↔ types.ts 守 |
 *
 * 为什么 `--generated` **不**比可选性：插件域 22 个后端模型全部字段带默认值，
 * Pydantic v2 一个 `required` 数组都不输出 → 照字面实现会得到「175 个字段全部可选」，
 * 首跑 170+ 条误报（设计 §2.1，可选性口径 A）。可选性继续由默认模式的
 * `compareInterface()` 守，`main()` 里两个模式都跑。
 *
 * 用法（在 apps/web 下执行）：
 *   node scripts/check-plugin-contract.mjs                        # 快照 ↔ types.ts（含可选性/枚举）
 *   node scripts/check-plugin-contract.mjs --json
 *   node scripts/check-plugin-contract.mjs --self-test             # 负样本自检（快照侧 8 条）
 *   node scripts/check-plugin-contract.mjs --generated             # gen ↔ types.ts + gen ↔ 快照（只比键集合）
 *   node scripts/check-plugin-contract.mjs --generated --self-test  # 负样本自检（gen 侧 3 条）
 *
 * 退出码（与既有两个扫描器一致，**故意如此**）：
 *   0 = 完全一致
 *   1 = 有差异；`--self-test` 下 1 表示**自检通过**（负样本全部被抓到）
 *   2 = 参数/文件错误（快照、types.ts 或 gen 读不到）
 */

import { readFileSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = resolve(HERE, '..');
const TYPES_PATH = join(WEB_ROOT, 'src/lib/types.ts');
const CONTRACT_PATH = join(WEB_ROOT, 'contracts/phase3.pl.json');
const GEN_PATH = join(WEB_ROOT, 'contracts/plugin-domain.gen.json');

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
 * 从 TypeScript 源码里解析「字面量联合类型」（`export type X = 'a' | 'b';`）。
 *
 * 只处理字符串字面量联合（本仓库枚举都是这种写法），遇到含泛型/交叉类型的别名直接跳过。
 *
 * @param {string} source - types.ts 源码
 * @returns {Map<string, string[]>} 类型名 → 字面量取值
 */
export function parseEnums(source) {
  const cleaned = source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1');

  const result = new Map();
  for (const match of cleaned.matchAll(/export type ([A-Za-z0-9_]+)\s*=\s*([\s\S]*?);/g)) {
    const name = match[1];
    const body = match[2];
    const values = [...body.matchAll(/'([^']+)'/g)].map((item) => item[1]);
    // 只收录「纯字符串字面量联合」，其它形态（对象/泛型）不参与枚举校验
    const stripped = body.replace(/'[^']*'/g, '').replace(/[\s|]/g, '');
    if (values.length > 0 && stripped === '') {
      result.set(name, values);
    }
  }
  return result;
}

/**
 * 比对枚举取值集合。
 *
 * @param {string} name - 类型名
 * @param {string[]} expected - 契约快照里的取值
 * @param {string[]|undefined} actual - 源码里解析到的取值
 * @returns {Array<string>} 差异说明
 */
export function compareEnum(name, expected, actual) {
  if (!actual) {
    return [`缺少枚举类型 ${name}`];
  }
  const problems = [];
  const expectedSet = new Set(expected);
  const actualSet = new Set(actual);
  for (const value of expectedSet) {
    if (!actualSet.has(value)) problems.push(`${name} 缺值「${value}」（契约有、类型没有）`);
  }
  for (const value of actualSet) {
    if (!expectedSet.has(value)) problems.push(`${name} 多值「${value}」（类型有、契约没有）`);
  }
  return problems;
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
export function compareAll(contract, interfaces, enums = new Map()) {
  const problems = [];
  let interfaceCount = 0;
  let fieldCount = 0;
  let enumCount = 0;

  for (const [name, expected] of Object.entries(contract.interfaces ?? {})) {
    interfaceCount += 1;
    fieldCount += (expected.required?.length ?? 0) + (expected.optional?.length ?? 0);
    problems.push(...compareInterface(name, expected, interfaces.get(name)));
  }

  for (const [name, expected] of Object.entries(contract.enums ?? {})) {
    enumCount += 1;
    problems.push(...compareEnum(name, expected, enums.get(name)));
  }

  return { problems, interfaceCount, fieldCount, enumCount };
}

// ---------------------------------------------------------------------------
// `--generated`（W-01 契约换源）：只比键集合，双向严格
// ---------------------------------------------------------------------------

/**
 * 把「前端键 → 后端键」的改名声明应用到生成契约的键集合上。
 *
 * 生成契约的键来自后端 OpenAPI（如 `CapabilityPolicyItem.enabled`），前端声明的是
 * 业务名（`org_switch_enabled`）。`gen.rename[接口名]` 的形态是
 * `{ <前端键>: <后端键> }`（附录 A-7），此处把它反向应用：后端键 → 前端键，
 * 这样后续比对两侧都是前端口径的键集合。
 *
 * @param {string[]} keys - 生成契约里该接口的键（后端口径）
 * @param {Record<string, string>|undefined} rename - 该接口的改名声明
 * @returns {string[]} 前端口径的键集合
 */
export function applyRename(keys, rename) {
  if (!rename) return [...keys];
  const backendToFrontend = new Map();
  for (const [frontendKey, backendKey] of Object.entries(rename)) {
    backendToFrontend.set(backendKey, frontendKey);
  }
  return keys.map((key) => backendToFrontend.get(key) ?? key);
}

/**
 * 只比键集合（不含可选性）。
 *
 * @param {string} name - 接口名
 * @param {string[]} expectedKeys - 生成契约给出的键集合（权威）
 * @param {{required: string[], optional: string[]}|undefined} actual - 被比对侧的字段
 * @param {string} side - 被比对侧的名字（进差异文案，如 `types.ts`）
 * @returns {Array<string>} 差异说明（空数组表示一致）
 */
export function compareKeySets(name, expectedKeys, actual, side) {
  if (!actual) {
    return [`缺少接口 ${name}（${side} 里没有，生成契约有 ${expectedKeys.length} 个键）`];
  }

  const expected = new Set(expectedKeys);
  const actualKeys = new Set([...(actual.required ?? []), ...(actual.optional ?? [])]);
  const problems = [];

  for (const key of expected) {
    if (!actualKeys.has(key)) {
      problems.push(`${name}.${key} 缺键：生成契约有、${side} 没有`);
    }
  }
  for (const key of actualKeys) {
    if (!expected.has(key)) {
      problems.push(`${name}.${key} 多键：${side} 有、生成契约没有`);
    }
  }

  return problems;
}

/**
 * 比对生成契约（`--generated` 模式）。
 *
 * 两个方向：
 * - A：`gen` ↔ `types.ts`（只比键集合）；
 * - B：`gen` ↔ `contracts/phase3.pl.json`（只比键集合）；
 *   快照里出现白名单之外的接口 → 红（防「删掉就没人管」），
 *   但 `gen.excluded` 声明的任务域与前端投影类型按设计排除。
 *
 * @param {{interfaces?: Record<string, {keys: string[]}>, rename?: Record<string, Record<string, string>>, excluded?: {task_domain?: string[], projection?: string[]}}} generated - 生成契约
 * @param {Map<string, {required: string[], optional: string[]}>} interfaces - types.ts 解析结果
 * @param {{interfaces?: Record<string, {required: string[], optional: string[]}>}} snapshot - 手写快照
 * @returns {{problems: Array<string>, interfaceCount: number, fieldCount: number}} 比对结果
 */
export function compareGenerated(generated, interfaces, snapshot) {
  const problems = [];
  const genInterfaces = generated.interfaces ?? {};
  const renames = generated.rename ?? {};
  const excluded = new Set([
    ...(generated.excluded?.task_domain ?? []),
    ...(generated.excluded?.projection ?? []),
  ]);

  let interfaceCount = 0;
  let fieldCount = 0;

  for (const [tsName, entry] of Object.entries(genInterfaces)) {
    interfaceCount += 1;
    const keys = applyRename(entry.keys ?? [], renames[tsName]);
    fieldCount += keys.length;

    problems.push(...compareKeySets(tsName, keys, interfaces.get(tsName), 'types.ts'));
    problems.push(...compareKeySets(tsName, keys, snapshot.interfaces?.[tsName], '手写快照'));
  }

  // 白名单声明之外、又不在排除表里的快照接口 → 红（否则「从 gen 里删掉」无人拦）
  for (const tsName of Object.keys(snapshot.interfaces ?? {})) {
    if (genInterfaces[tsName] || excluded.has(tsName)) continue;
    problems.push(`${tsName} 不在生成契约白名单里（快照有、gen 没有，且未声明为排除项）`);
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

/** 枚举契约样本 */
const ENUM_CONTRACT = { interfaces: {}, enums: { DemoStatus: ['a', 'b'] } };

/** 枚举负样本：各自只破坏一件事 */
const ENUM_NEGATIVE = [
  {
    name: '枚举缺值',
    expect: '缺值',
    source: "export type DemoStatus = 'a';",
  },
  {
    name: '枚举多值',
    expect: '多值',
    source: "export type DemoStatus = 'a' | 'b' | 'c';",
  },
  {
    name: '枚举类型整体缺失',
    expect: '缺少枚举类型',
    source: 'export const nothing = 1;',
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

  // 枚举：正样本必须通过
  const enumBase = compareAll(ENUM_CONTRACT, new Map(), parseEnums("export type DemoStatus = 'a' | 'b';"));
  if (enumBase.problems.length > 0) {
    failures.push(`枚举正样本误报：${enumBase.problems.join('；')}`);
  }

  for (const sample of ENUM_NEGATIVE) {
    const result = compareAll(ENUM_CONTRACT, new Map(), parseEnums(sample.source));
    if (!result.problems.some((problem) => problem.includes(sample.expect))) {
      failures.push(
        `枚举负样本漏抓「${sample.name}」：期望含「${sample.expect}」，实际 ${result.problems.join('；') || '无问题'}`,
      );
    }
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
      `负样本 ${NEGATIVE.length}/${NEGATIVE.length} 全部抓到（缺键/多键/双向可选性翻转/接口缺失）；` +
      `枚举正样本 1 条 0 误报、负样本 ${ENUM_NEGATIVE.length}/${ENUM_NEGATIVE.length} 抓到（缺值/多值/类型缺失）`,
  );
  return 1;
}

// ---------------------------------------------------------------------------
// `--generated` 自检：合成样本，不读真文件（CI 里没有 Python 也能验门禁没坏）
// ---------------------------------------------------------------------------

/** 生成契约正样本：与 `GEN_BASE_SOURCE` / `GEN_BASE_SNAPSHOT` 一致 */
const GEN_BASE = {
  interfaces: {
    DemoItem: { keys: ['plugin_id', 'name', 'has_update'] },
    RenamedItem: { keys: ['capability_key', 'enabled'] },
  },
  rename: { RenamedItem: { org_switch_enabled: 'enabled' } },
  excluded: { task_domain: ['DemoTask'], projection: ['DemoProjection'] },
};

/** types.ts 侧正样本（含改名后的前端口径键） */
const GEN_BASE_SOURCE = `
export interface DemoItem {
  plugin_id: string;
  name: string;
  has_update?: boolean;
}

export interface RenamedItem {
  capability_key: string;
  org_switch_enabled: boolean;
}

export interface DemoTask {
  task_id: string;
}

export interface DemoProjection {
  capability_key: string;
}
`;

/** 手写快照侧正样本：任务域/投影接口存在但按 `excluded` 跳过 */
const GEN_BASE_SNAPSHOT = {
  interfaces: {
    DemoItem: { required: ['plugin_id', 'name'], optional: ['has_update'] },
    RenamedItem: { required: ['capability_key', 'org_switch_enabled'], optional: [] },
    DemoTask: { required: ['task_id'], optional: [] },
    DemoProjection: { required: ['capability_key'], optional: [] },
  },
};

/**
 * 生成契约负样本：各自只破坏一件事。
 *
 * 差异文案的方向与快照模式**保持一致**（相对权威侧命名）：
 * 「缺键」= 权威侧（gen）有、被比对侧没有；「多键」= 被比对侧有、权威侧没有。
 */
const GEN_NEGATIVE = [
  {
    name: 'gen 多出一个前端没有的键',
    expect: '缺键',
    generated: {
      interfaces: { DemoItem: { keys: ['plugin_id', 'name', 'has_update', 'extra_field'] } },
    },
    source: GEN_BASE_SOURCE,
    snapshot: {
      interfaces: { DemoItem: { required: ['plugin_id', 'name'], optional: ['has_update'] } },
    },
  },
  {
    name: '前端多出一个 gen 没有的键（gen 落后）',
    expect: '多键',
    generated: { interfaces: { DemoItem: { keys: ['plugin_id'] } } },
    source: GEN_BASE_SOURCE,
    snapshot: {
      interfaces: { DemoItem: { required: ['plugin_id'], optional: [] } },
    },
  },
  {
    name: '接口整体缺失（前端类型里没有该接口）',
    expect: '缺少接口',
    generated: { interfaces: { MissingItem: { keys: ['a'] } } },
    source: GEN_BASE_SOURCE,
    snapshot: { interfaces: {} },
  },
];

/**
 * `--generated --self-test`：合成样本自检。
 *
 * @returns {number} 0 失败 / 1 通过（故意：通过=1，让门禁「失效即红」）
 */
function generatedSelfTest() {
  const failures = [];

  const base = compareGenerated(GEN_BASE, parseInterfaces(GEN_BASE_SOURCE), GEN_BASE_SNAPSHOT);
  if (base.problems.length > 0) {
    failures.push(`正样本误报：${base.problems.join('；')}`);
  }
  if (base.interfaceCount !== 2 || base.fieldCount !== 5) {
    failures.push(
      `正样本计数错误：期望 2 个接口 / 5 个字段，实际 ${base.interfaceCount} / ${base.fieldCount}`,
    );
  }

  // 改名声明必须真的生效：若 applyRename 失效，正样本会报「多键 enabled / 缺键 org_switch_enabled」
  const renamed = applyRename(['enabled'], { org_switch_enabled: 'enabled' });
  if (renamed.join(',') !== 'org_switch_enabled') {
    failures.push(`改名声明未生效：${JSON.stringify(renamed)}`);
  }

  // 快照里冒出白名单外接口（且未声明排除）必须红
  const straySnapshot = {
    interfaces: { ...GEN_BASE_SNAPSHOT.interfaces, StrayItem: { required: ['x'], optional: [] } },
  };
  const stray = compareGenerated(GEN_BASE, parseInterfaces(GEN_BASE_SOURCE), straySnapshot);
  if (!stray.problems.some((problem) => problem.includes('不在生成契约白名单里'))) {
    failures.push('白名单外快照接口漏抓（期望「不在生成契约白名单里」）');
  }

  for (const sample of GEN_NEGATIVE) {
    const result = compareGenerated(
      sample.generated,
      parseInterfaces(sample.source),
      sample.snapshot,
    );
    if (!result.problems.some((problem) => problem.includes(sample.expect))) {
      failures.push(
        `gen 负样本漏抓「${sample.name}」：期望命中「${sample.expect}」，实际 ${result.problems.join('；') || '无问题'}`,
      );
    }
  }

  if (failures.length > 0) {
    console.error(`SELF-TEST FAIL（generated，${failures.length}）`);
    for (const failure of failures) console.error('  -', failure);
    return 0;
  }

  console.info(
    `SELF-TEST PASS（generated）：正样本 2 个接口 / 5 个字段 0 误报（含改名声明）；` +
      `负样本 ${GEN_NEGATIVE.length}/${GEN_NEGATIVE.length} 全部抓到（缺键/多键/接口缺失）；` +
      `白名单外快照接口 1/1 抓到；改名声明生效`,
  );
  return 1;
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

/**
 * `--generated` 模式的正式比对。
 *
 * @returns {number} 0 / 1 / 2
 */
function runGenerated() {
  let generated;
  let snapshot;
  let source;
  try {
    generated = JSON.parse(readFileSync(GEN_PATH, 'utf8'));
  } catch (error) {
    console.error(
      `[plugin-contract] 读不到生成契约 ${relative(WEB_ROOT, GEN_PATH).replace(/\\/g, '/')}：`,
      error instanceof Error ? error.message : error,
    );
    console.error('[plugin-contract] 先跑 python deploy/export_plugin_contract.py 生成并提交；不静默回退到手写快照。');
    return 2;
  }

  try {
    source = readFileSync(TYPES_PATH, 'utf8');
    snapshot = JSON.parse(readFileSync(CONTRACT_PATH, 'utf8'));
  } catch (error) {
    console.error('[plugin-contract] 读取失败：', error instanceof Error ? error.message : error);
    return 2;
  }

  const interfaces = parseInterfaces(source);
  const { problems, interfaceCount, fieldCount } = compareGenerated(generated, interfaces, snapshot);
  const genLabel = relative(WEB_ROOT, GEN_PATH).replace(/\\/g, '/');

  if (problems.length === 0) {
    console.info(
      `生成契约比对：${interfaceCount} 个接口 / ${fieldCount} 个字段，键集合与 types.ts、手写快照三方一致（0 差异）` +
        `（真相源 ${genLabel}）`,
    );
  } else {
    console.info(
      `生成契约比对：${interfaceCount} 个接口 / ${fieldCount} 个字段，${problems.length} 处差异` +
        `（真相源 ${genLabel}，只比键集合；可选性由默认模式守）：`,
    );
    for (const problem of problems) console.info(`  - ${problem}`);
  }

  return problems.length > 0 ? 1 : 0;
}

function main(argv) {
  if (argv.includes('--generated')) {
    if (argv.includes('--self-test')) return generatedSelfTest();
    return runGenerated();
  }

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
  const enums = parseEnums(source);
  const { problems, interfaceCount, fieldCount, enumCount } = compareAll(contract, interfaces, enums);

  if (argv.includes('--json')) {
    console.info(JSON.stringify({ interfaceCount, fieldCount, enumCount, problems }, null, 2));
  } else if (problems.length === 0) {
    console.info(
      `检查 ${interfaceCount} 个接口 / ${fieldCount} 个字段 / ${enumCount} 个枚举，与契约快照完全一致（0 差异）` +
        `（快照 ${relative(WEB_ROOT, CONTRACT_PATH).replace(/\\/g, '/')}）`,
    );
  } else {
    console.info(`检查 ${interfaceCount} 个接口 / ${fieldCount} 个字段 / ${enumCount} 个枚举，${problems.length} 处差异：`);
    for (const problem of problems) console.info(`  - ${problem}`);
  }

  return problems.length > 0 ? 1 : 0;
}

process.exit(main(process.argv.slice(2)));
