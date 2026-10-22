#!/usr/bin/env node
/**
 * 能力使用一致性门禁（G-30，跨层静态校验）
 * ---------------------------------------------------------------------------
 * 校验对象：`apps/adapter/app/plugins/manifests/*.json`（6 份声明）。
 * 校验规则（双向）：
 *   A. **用了必须被授**（越权，红）：步骤的执行原语若需要授权能力，声明里必须有对应能力；
 *   B. **要了必须用**（多要权限，红）：声明了的每项能力，必须能在步骤或输入里找到用途。
 *
 * **同源要求**：枚举与映射**不在本脚本里复制一份**，而是直接解析后端
 * `apps/adapter/app/plugins/actions.py` 的常量（`ACTION_WHITELIST` /
 * `ACTION_CAPABILITY_HINTS` / `CAPABILITY_KEYS` / `CAPABILITY_ALTERNATIVES` /
 * `PERSONAL_CAPABILITIES` / `ORG_CAPABILITIES`）。后端改枚举 → 本门禁立刻跟着变，
 * 两边不会漂。解析不到常量时**直接失败（exit 2）**，不静默降级成本地副本。
 *
 * 用法（在 apps/web 下执行）：
 *   node scripts/check-capability-consistency.mjs
 *   node scripts/check-capability-consistency.mjs --warn-only   # 双向差异只告警（整改期用）
 *   node scripts/check-capability-consistency.mjs --json
 *   node scripts/check-capability-consistency.mjs --self-test   # 自检通过返回 1
 *
 * 退出码：0 = 完全一致；1 = 有违规（`--self-test` 下 1 表示自检通过）；2 = 输入缺失/解析失败
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = resolve(HERE, '..');
const REPO_ROOT = resolve(WEB_ROOT, '../..');
const ACTIONS_PATH = join(REPO_ROOT, 'apps/adapter/app/plugins/actions.py');
const MANIFEST_DIR = join(REPO_ROOT, 'apps/adapter/app/plugins/manifests');

/** 只读型工具操作前缀（用于区分 github 读与写） */
const READ_ONLY_OPERATION = /^(get|list|search|read|fetch|query)_/;

/** 通讯录/组织类操作特征（`org.directory.read`） */
const DIRECTORY_OPERATION = /(contact|department|member|user|directory|org)/i;

/** 对外发送类（`org.external.publish`）：这些渠道的发消息/发布动作 */
const OUTBOUND_CHANNELS = new Set(['wechat_mp', 'email', 'wecom', 'feishu']);
const OUTBOUND_OPERATIONS = new Set(['send_message', 'publish_article']);

/**
 * 解析 Python 里 `NAME ... = (` 或 `NAME ... = {` 的赋值体文本。
 *
 * @param {string} source - python 源码
 * @param {string} name - 常量名
 * @returns {string|null} 括号内的文本；找不到返回 null
 */
export function extractPyLiteral(source, name) {
  const assign = new RegExp(`${name}\\s*(?::[^=]*)?=\\s*([({])`);
  const match = assign.exec(source);
  if (!match) return null;

  const open = match[1];
  const close = open === '(' ? ')' : '}';
  let index = match.index + match[0].length;
  let depth = 1;
  let body = '';
  while (index < source.length && depth > 0) {
    const ch = source[index];
    if (ch === open) depth += 1;
    else if (ch === close) {
      depth -= 1;
      if (depth === 0) break;
    }
    body += ch;
    index += 1;
  }
  return depth === 0 ? body : null;
}

/**
 * 从字面量体里抽取顶层字符串元素（tuple / list / set）。
 *
 * @param {string} body - 括号内文本
 * @returns {Array<string>} 字符串元素
 */
export function parseStringTuple(body) {
  const values = [];
  let depth = 0;
  let current = '';
  for (const ch of body) {
    if (ch === '(' || ch === '[' || ch === '{') depth += 1;
    if (ch === ')' || ch === ']' || ch === '}') depth -= 1;
    if (depth === 0 && ch === ',') {
      const found = [...current.matchAll(/"([^"]+)"|'([^']+)'/g)].map(
        (item) => item[1] ?? item[2],
      );
      if (found.length > 0) values.push(...found);
      current = '';
      continue;
    }
    current += ch;
  }
  const tail = [...current.matchAll(/"([^"]+)"|'([^']+)'/g)].map((item) => item[1] ?? item[2]);
  values.push(...tail);
  return values;
}

/**
 * 解析 `{ "key": ("a", "b"), ... }` 形式的映射。
 *
 * @param {string} body - 花括号内文本
 * @returns {Record<string, string[]>} key → 字符串数组
 */
export function parseTupleMap(body) {
  const map = {};
  const entryPattern = /"([^"]+)"\s*:\s*\(([^)]*)\)/g;
  let match;
  while ((match = entryPattern.exec(body)) !== null) {
    map[match[1]] = parseStringTuple(match[2]);
  }
  return map;
}

/**
 * 读取后端的枚举与映射（同源）。
 *
 * @param {string} source - actions.py 源码
 * @returns {{actions: string[], hints: Record<string, string[]>, capabilities: string[], alternatives: string[][], personal: string[], org: string[]}} 常量集合
 */
export function parseBackendConstants(source) {
  const actionBody = extractPyLiteral(source, 'ACTION_WHITELIST');
  const hintBody = extractPyLiteral(source, 'ACTION_CAPABILITY_HINTS');
  const capabilityBody = extractPyLiteral(source, 'CAPABILITY_KEYS');
  const altBody = extractPyLiteral(source, 'CAPABILITY_ALTERNATIVES');
  const personalBody = extractPyLiteral(source, 'PERSONAL_CAPABILITIES');
  const orgBody = extractPyLiteral(source, 'ORG_CAPABILITIES');

  if (!actionBody || !hintBody || !capabilityBody) {
    throw new Error(
      '无法从 actions.py 解析 ACTION_WHITELIST / ACTION_CAPABILITY_HINTS / CAPABILITY_KEYS',
    );
  }

  const alternatives = [];
  if (altBody) {
    for (const item of altBody.matchAll(/\(([^()]*)\)/g)) {
      const keys = parseStringTuple(item[1]);
      if (keys.length > 1) alternatives.push(keys);
    }
  }

  return {
    actions: parseStringTuple(actionBody),
    hints: parseTupleMap(hintBody),
    capabilities: parseStringTuple(capabilityBody),
    alternatives,
    personal: personalBody ? parseStringTuple(personalBody) : [],
    org: orgBody ? parseStringTuple(orgBody) : [],
  };
}

/**
 * 判断某步骤是否「消费」了某项能力。
 *
 * @param {{seq: number, action: string, tool_type?: string, operation?: string}} step - 步骤声明
 * @param {string} capability - 能力 key
 * @param {{hints: Record<string, string[]>, alternatives: string[][]}} constants - 后端常量
 * @returns {boolean} 有消费返回 true
 */
export function stepConsumesCapability(step, capability, constants) {
  const hints = constants.hints[step.action] ?? [];
  // 同组能力（CAPABILITY_ALTERNATIVES）任一即可满足该原语
  const group = constants.alternatives.find((keys) => keys.includes(capability));
  const coveredByHints = hints.includes(capability) || (group ? hints.some((key) => group.includes(key)) : false);
  if (coveredByHints) return true;

  const operation = step.operation ?? '';
  const toolType = step.tool_type ?? '';

  if (capability === 'org.repo.write') {
    return step.action === 'tool' && toolType === 'github' && !READ_ONLY_OPERATION.test(operation);
  }
  if (capability === 'org.external.publish') {
    return step.action === 'tool' && OUTBOUND_CHANNELS.has(toolType) && OUTBOUND_OPERATIONS.has(operation);
  }
  if (capability === 'org.directory.read') {
    return (
      step.action === 'tool' &&
      READ_ONLY_OPERATION.test(operation) &&
      DIRECTORY_OPERATION.test(operation)
    );
  }
  return false;
}

/**
 * 找出步骤需要、但声明未授予的能力（越权检查）。
 *
 * @param {{seq: number, action: string, tool_type?: string, operation?: string}} step - 步骤声明
 * @param {Set<string>} declared - 已声明能力
 * @param {{hints: Record<string, string[]>, alternatives: string[][]}} constants - 后端常量
 * @returns {Array<string>} 缺失能力
 */
export function missingCapabilitiesForStep(step, declared, constants) {
  const missing = [];
  const hints = constants.hints[step.action] ?? [];

  // 原语级：hints 属于「同组任一」，组内一个都没声明才算缺
  if (hints.length > 0) {
    const satisfied = hints.some((key) => declared.has(key));
    if (!satisfied) {
      // 组内所有能力都没声明 → 报第一个作为代表
      missing.push(hints[0]);
    }
  }

  const operation = step.operation ?? '';
  const toolType = step.tool_type ?? '';
  if (step.action === 'tool' && toolType === 'github' && !READ_ONLY_OPERATION.test(operation)) {
    if (!declared.has('org.repo.write')) missing.push('org.repo.write');
  }
  if (
    step.action === 'tool' &&
    OUTBOUND_CHANNELS.has(toolType) &&
    OUTBOUND_OPERATIONS.has(operation)
  ) {
    if (!declared.has('org.external.publish')) missing.push('org.external.publish');
  }
  if (
    step.action === 'tool' &&
    READ_ONLY_OPERATION.test(operation) &&
    DIRECTORY_OPERATION.test(operation)
  ) {
    if (!declared.has('org.directory.read')) missing.push('org.directory.read');
  }
  return [...new Set(missing)];
}

/**
 * 检查单份声明。
 *
 * @param {{plugin_id: string, capabilities?: Array<{key: string}|string>, inputs?: Array<{type: string}>, steps?: Array<object>}} manifest - 声明
 * @param {{actions: string[], capabilities: string[], hints: Record<string, string[]>, alternatives: string[][]}} constants - 后端常量
 * @returns {{errors: string[], warnings: string[]}} 问题
 */
export function checkManifest(manifest, constants) {
  const errors = [];
  const warnings = [];
  const pluginId = manifest.plugin_id ?? '(未命名)';

  const declaredList = (manifest.capabilities ?? []).map((item) =>
    typeof item === 'string' ? item : item?.key,
  );
  const declared = new Set(declaredList.filter(Boolean));
  const steps = manifest.steps ?? [];
  const hasFileInput = (manifest.inputs ?? []).some((input) => input.type === 'file');

  // 1) 能力 key 必须在后端枚举内
  for (const key of declared) {
    if (!constants.capabilities.includes(key)) {
      errors.push(`${pluginId}: 能力「${key}」不在后端枚举 CAPABILITY_KEYS 内`);
    }
  }

  // 2) 步骤原语必须在白名单内
  for (const step of steps) {
    if (!constants.actions.includes(step.action)) {
      errors.push(`${pluginId}: 第 ${step.seq} 步原语「${step.action}」不在 ACTION_WHITELIST 内`);
    }
  }

  // 3) 用了必须被授（越权）
  for (const step of steps) {
    for (const capability of missingCapabilitiesForStep(step, declared, constants)) {
      errors.push(
        `${pluginId}: 第 ${step.seq} 步（${step.action}${
          step.tool_type ? ` ${step.tool_type}.${step.operation}` : ''
        }）需要「${capability}」，但声明里没有`,
      );
    }
  }

  // 4) 要了必须用（多要权限）
  for (const capability of declared) {
    const usedByStep = steps.some((step) => stepConsumesCapability(step, capability, constants));
    const usedByInput = capability === 'personal.file.read' && hasFileInput;
    const isPersonalBaseline = capability === 'personal.tool.use' && steps.some((s) => s.action === 'tool');
    if (!usedByStep && !usedByInput && !isPersonalBaseline) {
      warnings.push(
        `${pluginId}: 声明了「${capability}」，但没有步骤或输入用到它（多要权限）`,
      );
    }
  }

  return { errors, warnings };
}

// ---------------------------------------------------------------------------
// 自检
// ---------------------------------------------------------------------------

/** 合成常量（形态与后端 actions.py 一致，但只用于自检） */
const DEMO_CONSTANTS = {
  actions: ['understand', 'compose', 'retrieve', 'tool', 'render_file', 'summarize'],
  hints: { retrieve: ['personal.knowledge.read', 'personal.file.read'], tool: ['personal.tool.use'] },
  capabilities: [
    'personal.knowledge.read',
    'personal.tool.use',
    'personal.file.read',
    'org.directory.read',
    'org.repo.write',
    'org.external.publish',
  ],
  alternatives: [['personal.knowledge.read', 'personal.file.read']],
  personal: ['personal.knowledge.read', 'personal.tool.use', 'personal.file.read'],
  org: ['org.directory.read', 'org.repo.write', 'org.external.publish'],
};

/** 正样本：一致 */
const DEMO_OK = {
  plugin_id: 'plg_demo',
  capabilities: [{ key: 'personal.knowledge.read' }, { key: 'personal.tool.use' }],
  inputs: [{ key: 'topic', type: 'text' }],
  steps: [
    { seq: 1, action: 'retrieve' },
    { seq: 2, action: 'tool', tool_type: 'feishu', operation: 'create_doc' },
    { seq: 3, action: 'compose' },
  ],
};

/** 负样本 */
const DEMO_NEGATIVE = [
  {
    name: '用了未授（retrieve 没要读取能力）',
    expect: '需要「personal.knowledge.read」',
    manifest: {
      plugin_id: 'plg_bad_grant',
      capabilities: [{ key: 'personal.tool.use' }],
      steps: [{ seq: 1, action: 'retrieve' }],
    },
  },
  {
    name: '用了未授（github 写没要 repo.write）',
    expect: 'org.repo.write',
    manifest: {
      plugin_id: 'plg_bad_repo',
      capabilities: [{ key: 'personal.tool.use' }],
      steps: [{ seq: 1, action: 'tool', tool_type: 'github', operation: 'create_pull_request' }],
    },
  },
  {
    name: '要了没用（声明知识读取但只 compose）',
    expect: '多要权限',
    manifest: {
      plugin_id: 'plg_bad_over',
      capabilities: [{ key: 'personal.knowledge.read' }],
      steps: [{ seq: 1, action: 'compose' }],
    },
  },
  {
    name: '原语不在白名单',
    expect: '不在 ACTION_WHITELIST',
    manifest: {
      plugin_id: 'plg_bad_action',
      capabilities: [],
      steps: [{ seq: 1, action: 'run_script' }],
    },
  },
  {
    name: '能力 key 不在枚举',
    expect: '不在后端枚举',
    manifest: {
      plugin_id: 'plg_bad_key',
      capabilities: [{ key: 'org.unknown.capability' }],
      steps: [{ seq: 1, action: 'compose' }],
    },
  },
];

/**
 * 自检。
 *
 * @returns {number} 0 失败 / 1 通过（故意：通过=1）
 */
function selfTest() {
  const failures = [];

  const ok = checkManifest(DEMO_OK, DEMO_CONSTANTS);
  if (ok.errors.length > 0 || ok.warnings.length > 0) {
    failures.push(`正样本误报：${[...ok.errors, ...ok.warnings].join('；')}`);
  }

  // 同组能力（knowledge.read / file.read）任一即可满足 retrieve：只声明 file.read 也应通过
  const alt = checkManifest(
    {
      plugin_id: 'plg_alt',
      capabilities: [{ key: 'personal.file.read' }],
      steps: [{ seq: 1, action: 'retrieve' }],
    },
    DEMO_CONSTANTS,
  );
  if (alt.errors.length > 0) {
    failures.push(`同组能力误报：${alt.errors.join('；')}`);
  }

  // 文件输入可证明 personal.file.read 有用（即使没有 retrieve 步）
  const fileInput = checkManifest(
    {
      plugin_id: 'plg_file',
      capabilities: [{ key: 'personal.file.read' }],
      inputs: [{ key: 'resumes', type: 'file' }],
      steps: [{ seq: 1, action: 'compose' }],
    },
    DEMO_CONSTANTS,
  );
  if (fileInput.warnings.length > 0) {
    failures.push(`文件输入未证明能力用途：${fileInput.warnings.join('；')}`);
  }

  for (const sample of DEMO_NEGATIVE) {
    const result = checkManifest(sample.manifest, DEMO_CONSTANTS);
    const all = [...result.errors, ...result.warnings];
    if (!all.some((message) => message.includes(sample.expect))) {
      failures.push(
        `负样本漏抓「${sample.name}」：期望含「${sample.expect}」，实际 ${all.join('；') || '无问题'}`,
      );
    }
  }

  // 解析器自检：从「类 python 文本」里解出常量
  const pySample = `
ACTION_WHITELIST: Final[tuple[str, ...]] = (
    "understand",
    "tool",
)

ACTION_CAPABILITY_HINTS: Final[dict[str, tuple[str, ...]]] = {
    "tool": ("personal.tool.use",),
}

CAPABILITY_KEYS: Final[tuple[str, ...]] = (
    "personal.tool.use",
    "org.repo.write",
)

CAPABILITY_ALTERNATIVES: Final[tuple[tuple[str, ...], ...]] = (
    ("personal.knowledge.read", "personal.file.read"),
)
`;
  const parsed = parseBackendConstants(pySample);
  if (parsed.actions.join(',') !== 'understand,tool') {
    failures.push(`ACTION_WHITELIST 解析错误：${parsed.actions.join(',')}`);
  }
  if ((parsed.hints.tool ?? []).join(',') !== 'personal.tool.use') {
    failures.push(`ACTION_CAPABILITY_HINTS 解析错误：${JSON.stringify(parsed.hints)}`);
  }
  if (parsed.alternatives.length !== 1) {
    failures.push(`CAPABILITY_ALTERNATIVES 解析错误：${JSON.stringify(parsed.alternatives)}`);
  }

  if (failures.length > 0) {
    console.error(`SELF-TEST FAIL（${failures.length}）`);
    for (const failure of failures) console.error('  -', failure);
    return 0;
  }

  console.info(
    `SELF-TEST PASS：正样本 3 条 0 误报（含同组能力、文件输入证明）；` +
      `负样本 ${DEMO_NEGATIVE.length}/${DEMO_NEGATIVE.length} 全部抓到（越权/多要/非法原语/非法能力键）；` +
      `后端常量解析 3 项正确`,
  );
  return 1;
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

function main(argv) {
  if (argv.includes('--self-test')) return selfTest();

  const warnOnly = argv.includes('--warn-only');

  let actionsSource;
  let manifestFiles;
  try {
    actionsSource = readFileSync(ACTIONS_PATH, 'utf8');
    manifestFiles = readdirSync(MANIFEST_DIR)
      .filter((name) => name.endsWith('.json'))
      .map((name) => join(MANIFEST_DIR, name));
  } catch (error) {
    console.error('[capability] 读取后端声明失败：', error instanceof Error ? error.message : error);
    return 2;
  }

  let constants;
  try {
    constants = parseBackendConstants(actionsSource);
  } catch (error) {
    console.error('[capability] 解析后端常量失败：', error instanceof Error ? error.message : error);
    return 2;
  }

  const errors = [];
  const warnings = [];
  const summary = [];

  for (const file of manifestFiles) {
    let manifest;
    try {
      manifest = JSON.parse(readFileSync(file, 'utf8'));
    } catch (error) {
      errors.push(`${relative(REPO_ROOT, file)}: JSON 解析失败`);
      continue;
    }
    const result = checkManifest(manifest, constants);
    errors.push(...result.errors.map((message) => `${relative(REPO_ROOT, file)} → ${message}`));
    warnings.push(...result.warnings.map((message) => `${relative(REPO_ROOT, file)} → ${message}`));
    summary.push({
      plugin: manifest.plugin_id ?? relative(REPO_ROOT, file),
      capabilities: (manifest.capabilities ?? []).length,
      steps: (manifest.steps ?? []).length,
    });
  }

  if (argv.includes('--json')) {
    console.info(JSON.stringify({ summary, errors, warnings, warnOnly }, null, 2));
  } else {
    for (const message of errors) console.info(`  ✗ ${message}`);
    for (const message of warnings) console.info(`  ! ${message}`);
    console.info(
      `[capability] 声明 ${manifestFiles.length} 份（能力枚举 ${constants.capabilities.length} 项 / 原语 ${constants.actions.length} 项）：` +
        `越权或多要权限 ${errors.length} 处、多余能力 ${warnings.length} 处`,
    );
    if (warnOnly && errors.length > 0) {
      console.info('[capability] --warn-only：本次仅告警，不判失败（整改期过渡用）');
    }
  }

  if (warnOnly) return 0;
  return errors.length > 0 || warnings.length > 0 ? 1 : 0;
}

process.exit(main(process.argv.slice(2)));
