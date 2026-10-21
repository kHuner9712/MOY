#!/usr/bin/env node
/**
 * 占位词扫描器（G-17 阶段门禁）
 * ---------------------------------------------------------------------------
 * 依据：`docs/prd-phase2-increment.md` 第 7 章 + 阶段二验收口径 ——
 * 界面文案里不允许出现「暂不支持 / 敬请期待」这类**占位式说法**：
 * 用户看到这类话术时无法判断「是我的问题、还是产品没做完」，属于体验缺陷。
 *
 * 允许的写法：说清「为什么不行 + 下一步怎么做」，例如
 *   「当前文件类型无法上传，请换用 PDF、Word 或 TXT」
 *   「该工具尚未连接，请先在设置中完成连接」
 *
 * 用法（在 apps/web 下执行）：
 *   node scripts/check-no-placeholders.mjs                # 扫 src 全部 ts/tsx
 *   node scripts/check-no-placeholders.mjs <file...>      # 只扫指定文件
 *   node scripts/check-no-placeholders.mjs --json         # JSON 输出（供报告消费）
 *   node scripts/check-no-placeholders.mjs --self-test    # 负样本自检
 *
 * 退出码约定（与 check-banned-terms.mjs 保持一致的「故意设计」）：
 *   0 = 无命中（可放行）
 *   1 = 有命中（拦截）；`--self-test` 下 1 表示**自检通过**（负样本全部被抓到）
 *   2 = 参数/文件错误
 *
 * 扫描范围规则（与禁词扫描器同源思路，避免误报把门禁拖死）：
 *   1. 只扫字符串字面量 + 模板字面量 + JSX 文本节点，**不扫注释与标识符**；
 *   2. 跳过 JSX 不可见属性（className/id/href 等），但 placeholder/title/aria-label 参与扫描；
 *   3. 子串匹配（占位词均为中文，不会与 ASCII 标识符混淆）。
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = resolve(HERE, '..');
const SRC_ROOT = join(WEB_ROOT, 'src');

/**
 * 占位词表（子串匹配）。
 *
 * 只收录「对用户没有信息量」的占位说法；像「该格式无法上传，请换用 PDF」
 * 这类**给出了出路**的文案不在禁列。
 */
export const PLACEHOLDER_TERMS = [
  '暂不支持',
  '暂未支持',
  '尚未支持',
  '敬请期待',
  '稍后开放',
  '即将上线',
  '即将开放',
  '正在开发',
  '开发中',
  '后续版本提供',
];

/** JSX 中与用户可见无关的属性（这些位置出现占位词不算文案） */
const INVISIBLE_ATTRS = new Set([
  'classname',
  'class',
  'style',
  'id',
  'href',
  'src',
  'htmlfor',
  'role',
  'type',
  'key',
  'onclick',
  'onchange',
  'onsubmit',
  'd',
  'viewbox',
  'fill',
  'stroke',
  'xmlns',
  'width',
  'height',
  'target',
  'rel',
]);

/** 去掉注释（保留换行与偏移，便于定位行号） */
function blankComments(src) {
  let out = '';
  let i = 0;
  while (i < src.length) {
    const c = src[i];
    const n = src[i + 1];
    if (c === '/' && n === '/') {
      while (i < src.length && src[i] !== '\n') {
        out += ' ';
        i += 1;
      }
    } else if (c === '/' && n === '*') {
      while (i < src.length && !(src[i] === '*' && src[i + 1] === '/')) {
        out += src[i] === '\n' ? '\n' : ' ';
        i += 1;
      }
      out += '  ';
      i += 2;
    } else if (c === '\'' || c === '"' || c === '`') {
      const quote = c;
      out += c;
      i += 1;
      while (i < src.length) {
        if (src[i] === '\\') {
          out += src[i] + (src[i + 1] ?? '');
          i += 2;
          continue;
        }
        out += src[i];
        if (src[i] === quote) {
          i += 1;
          break;
        }
        i += 1;
      }
    } else {
      out += c;
      i += 1;
    }
  }
  return out;
}

/** 去掉 import/export ... from 行（模块路径不是文案） */
function blankImports(src) {
  return src
    .replace(/^\s*(import|export)\b[^\n]*?from\s*['"][^'"]*['"]\s*;?\s*$/gm, (m) =>
      ' '.repeat(m.length),
    )
    .replace(/^\s*import\s*['"][^'"]*['"]\s*;?\s*$/gm, (m) => ' '.repeat(m.length));
}

/** 取字符串字面量所属的 JSX 属性名（不在属性位置返回 null） */
function getJsxAttr(src, quoteStart) {
  let j = quoteStart - 1;
  while (j >= 0 && /\s/.test(src[j])) j -= 1;
  if (src[j] !== '=') return null;
  j -= 1;
  while (j >= 0 && /\s/.test(src[j])) j -= 1;
  if (!/[A-Za-z0-9_$-]/.test(src[j] ?? '')) return null;
  const end = j + 1;
  let k = j;
  while (k >= 0 && /[A-Za-z0-9_$-]/.test(src[k])) k -= 1;
  const attr = src.slice(k + 1, end).toLowerCase();
  for (let m = k; m >= 0; m -= 1) {
    if (src[m] === '<') return attr;
    if (src[m] === '>' || src[m] === ';' || src[m] === '}') return null;
  }
  return null;
}

/** 判断该属性是否与用户可见文案无关 */
function isInvisibleAttr(attr) {
  return attr !== null && (INVISIBLE_ATTRS.has(attr) || attr.startsWith('data-'));
}

/** 抽取模板 `${...}` 表达式内部的字符串字面量（这些同样是文案） */
function extractQuotedStrings(chunk, baseOffset, out) {
  let i = 0;
  while (i < chunk.length) {
    const q = chunk[i];
    if (q === '\'' || q === '"') {
      const contentStart = i + 1;
      i += 1;
      while (i < chunk.length) {
        if (chunk[i] === '\\') {
          i += 2;
          continue;
        }
        if (chunk[i] === q) break;
        i += 1;
      }
      out.push({ text: chunk.slice(contentStart, i), start: baseOffset + contentStart });
      i += 1;
      continue;
    }
    i += 1;
  }
}

/**
 * 抽取待检文本片段：字符串字面量 + 模板静态部分 + `${}` 内字面量 + JSX 文本节点。
 *
 * @param {string} raw - 源码
 * @returns {Array<{text: string, start: number, kind: string}>} 片段列表
 */
export function extractSegments(raw) {
  const src = blankImports(blankComments(raw));
  const segs = [];
  let i = 0;
  while (i < src.length) {
    const c = src[i];

    if (c === '\'' || c === '"') {
      const start = i;
      const contentStart = i + 1;
      i += 1;
      while (i < src.length) {
        if (src[i] === '\\') {
          i += 2;
          continue;
        }
        if (src[i] === c) break;
        i += 1;
      }
      const text = src.slice(contentStart, i);
      if (!isInvisibleAttr(getJsxAttr(src, start))) {
        segs.push({ text, start: contentStart, kind: 'string' });
      }
      i += 1;
      continue;
    }

    if (c === '`') {
      const contentStart = i + 1;
      i += 1;
      while (i < src.length) {
        if (src[i] === '\\') {
          i += 2;
          continue;
        }
        if (src[i] === '`') break;
        i += 1;
      }
      const inner = src.slice(contentStart, i);
      segs.push({
        text: inner.replace(/\$\{[^}]*\}/g, ' '),
        start: contentStart,
        kind: 'template',
      });
      for (const m of inner.matchAll(/\$\{([^}]*)\}/g)) {
        extractQuotedStrings(m[1], contentStart + m.index + 2, segs);
      }
      i += 1;
      continue;
    }

    if (c === '>') {
      const prev = src[i - 1];
      const next = src[i + 1];
      if (prev !== '=' && next !== '=' && next !== '>' && !/[)\]};]/.test(prev ?? '')) {
        const contentStart = i + 1;
        let j = contentStart;
        while (j < src.length && src[j] !== '<' && src[j] !== '{') j += 1;
        const text = src.slice(contentStart, j);
        const trimmed = text.trim();
        const looksLikeCode =
          /[;=&|?]/.test(trimmed) || trimmed.length === 0 || trimmed.length > 200;
        if (!looksLikeCode) segs.push({ text, start: contentStart, kind: 'jsx-text' });
      }
    }
    i += 1;
  }
  return segs;
}

/**
 * 在单个片段里找出所有占位词命中。
 *
 * @param {string} text - 片段文本
 * @returns {Array<{term: string, index: number}>} 命中列表
 */
export function findHits(text) {
  const hits = [];
  for (const term of PLACEHOLDER_TERMS) {
    let idx = text.indexOf(term);
    while (idx !== -1) {
      hits.push({ term, index: idx });
      idx = text.indexOf(term, idx + term.length);
    }
  }
  return hits;
}

/** 建立「偏移 → 行号」映射 */
function lineIndex(src) {
  const starts = [0];
  for (let i = 0; i < src.length; i += 1) if (src[i] === '\n') starts.push(i + 1);
  return (offset) => {
    let lo = 0;
    let hi = starts.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (starts[mid] <= offset) lo = mid;
      else hi = mid - 1;
    }
    return lo + 1;
  };
}

/** 递归收集 src 下的 ts/tsx 文件 */
function walk(dir, acc = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) walk(p, acc);
    else if (/\.(ts|tsx)$/.test(name) && !name.endsWith('.d.ts')) acc.push(p);
  }
  return acc;
}

/** 扫描单个文件 */
function scanFile(file) {
  const raw = readFileSync(file, 'utf8');
  const lineOf = lineIndex(raw);
  const results = [];
  for (const seg of extractSegments(raw)) {
    const hits = findHits(seg.text);
    if (hits.length === 0) continue;
    const lead = Math.max(0, seg.text.search(/\S/));
    const line = lineOf(seg.start + (seg.text.search(/\S/) === -1 ? 0 : lead));
    const snippet = seg.text.trim().replace(/\s+/g, ' ').slice(0, 80);
    for (const hit of hits) {
      results.push({ file, line, term: hit.term, kind: seg.kind, snippet });
    }
  }
  return results;
}

// ---------------------------------------------------------------------------
// 自检（负样本必须被抓到，正样本必须不误报）
// ---------------------------------------------------------------------------

/** 负样本：必须命中 */
const NEGATIVE = [
  { src: "toast.error('暂不支持该格式');", term: '暂不支持' },
  { src: 'const s = `插件敬请期待`;', term: '敬请期待' },
  { src: "const t = `${ok ? '敬请期待后续版本' : ''}`;", term: '敬请期待' },
  { src: 'const hint = "该功能即将上线";', term: '即将上线' },
  { src: 'const text = `岗位插件正在开发中`;', term: '正在开发' },
  { src: 'const a = <p>该能力稍后开放</p>;', term: '稍后开放' },
];

/** 正样本：不得命中（含「无法上传但给出路」「尚未连接但给出路」） */
const POSITIVE = [
  '当前文件类型无法上传，请换用 PDF、Word 或 TXT',
  '该工具尚未连接，请先在设置中完成连接',
  '该知识库正在处理中，请稍后再试',
  '当前套餐不包含该能力，升级后即可使用',
  '登录状态已失效，请重新登录',
];

/** 注释与标识符不得命中 */
const IGNORED = [
  '// 暂不支持旧格式，历史数据仍在\nconst a = 1;',
  'const placeholderHint = "暂不支持"; // 标识符名不算，值算', // 注：值仍应命中，见下方断言
];

/**
 * 自检：负样本全中、正样本零误报。
 *
 * 关键点：**负样本必须能被抓到**，否则这个门禁就是「永远绿灯」的假门禁。
 * 因此自检通过时同样返回 1（与扫描命中同码），使任何让扫描器失效的改动都会让 CI 红。
 *
 * @returns 0/1 退出码
 */
function selfTest() {
  const failures = [];

  for (const sample of NEGATIVE) {
    const segs = extractSegments(sample.src);
    const hits = segs.flatMap((seg) => findHits(seg.text));
    if (!hits.some((hit) => hit.term === sample.term)) {
      failures.push(`负样本未命中：${sample.src}（期望命中「${sample.term}」）`);
    }
  }

  for (const text of POSITIVE) {
    const hits = findHits(text);
    if (hits.length > 0) {
      failures.push(`正样本误报：「${text}」命中 ${hits.map((h) => h.term).join(',')}`);
    }
  }

  // 注释里的占位词不算文案（用户看不到）
  const commentOnly = extractSegments('// 暂不支持\nconst a = 1;');
  if (commentOnly.some((seg) => findHits(seg.text).length > 0)) {
    failures.push('注释被误判为文案');
  }

  // 字符串值里的占位词必须被抓到（防止「只在注释里宽松」被滥用）
  const inString = extractSegments(IGNORED[1]).flatMap((seg) => findHits(seg.text));
  if (!inString.some((hit) => hit.term === '暂不支持')) {
    failures.push('字符串字面量里的占位词未命中');
  }

  if (failures.length > 0) {
    console.error(`SELF-TEST FAIL（${failures.length}）`);
    for (const failure of failures) console.error('  -', failure);
    return 0;
  }

  console.info(
    `SELF-TEST PASS：负样本 ${NEGATIVE.length}/${NEGATIVE.length} 命中；` +
      `正样本 ${POSITIVE.length} 条 0 误报；注释不参与判定；字符串值仍参与判定`,
  );
  return 1;
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

function main(argv) {
  if (argv.includes('--self-test')) return selfTest();

  const json = argv.includes('--json');
  const files = argv.filter((arg) => !arg.startsWith('--'));
  const targets = files.length
    ? files.map((file) => resolve(process.cwd(), file))
    : walk(SRC_ROOT);

  let all = [];
  for (const file of targets) all = all.concat(scanFile(file));
  all.sort((a, b) => (a.file === b.file ? a.line - b.line : a.file < b.file ? -1 : 1));

  if (json) {
    console.info(
      JSON.stringify(
        all.map((hit) => ({ ...hit, file: relative(WEB_ROOT, hit.file) })),
        null,
        2,
      ),
    );
  } else {
    for (const hit of all) {
      console.info(`${relative(WEB_ROOT, hit.file)}:${hit.line}:${hit.term}:${hit.snippet}`);
    }
    if (all.length > 0) {
      const byFile = new Map();
      for (const hit of all) {
        const key = relative(WEB_ROOT, hit.file);
        byFile.set(key, (byFile.get(key) || 0) + 1);
      }
      console.info(`\n合计 ${all.length} 处占位词命中，涉及 ${byFile.size} 个文件`);
      for (const [key, value] of [...byFile].sort()) console.info(`  ${key}: ${value}`);
    } else {
      console.info(`扫描 ${targets.length} 个文件，0 命中`);
    }
  }
  return all.length ? 1 : 0;
}

process.exit(main(process.argv.slice(2)));
