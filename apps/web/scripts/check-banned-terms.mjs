#!/usr/bin/env node
/**
 * 禁词扫描器（阶段二前置介入版）
 * ---------------------------------------------------------------------------
 * 依据：`docs/prd-phase2-increment.md` 第 7 章「术语对照表（红线检查清单）」
 * 判定：左列技术词出现在**用户可见文案**即为缺陷；右列业务语言为唯一允许写法。
 *
 * 用法（在 apps/web 下执行）：
 *   node scripts/check-banned-terms.mjs                 # 扫 src 全部 ts/tsx
 *   node scripts/check-banned-terms.mjs <file...>       # 只扫指定文件
 *   node scripts/check-banned-terms.mjs --json          # 输出 JSON（供 CI/报告消费）
 *   node scripts/check-banned-terms.mjs --update-words  # 从 PRD 第 7 章重新生成词表常量
 *   node scripts/check-banned-terms.mjs --check-words   # 校验词表与 PRD 是否漂移（漂移退出码 3）
 *   node scripts/check-banned-terms.mjs --self-test     # 负样本自检
 *   node scripts/check-banned-terms.mjs --built         # G-16：扫 .next 产物可见文本（10 个业务路由）
 *
 * 退出码约定（与 docs/qa-phase2-test-plan.md G-14/G-15 一致，**故意如此**）：
 *   0 = 无命中（可放行）
 *   1 = 有命中（拦截）；`--self-test` 下 1 表示**自检通过**（12 条负样本全部命中），
 *       因为"扫描器能抓到东西"与"扫到了违规"在语义上同为"有命中"，
 *       这样任何导致扫描器失效的改动都不会被误判为成功。
 *   2 = 参数/文件错误（`--built` 下为「产物缺失，先 build」）
 *   3 = 词表与 PRD 漂移
 *
 * 设计红线（避免门禁被误报噪声拖死）：
 *   1. 只扫「字符串字面量 + 模板字面量 + JSX 文本节点」，**不扫代码标识符**
 *      （`segment_count`、`token` 作为变量名不构成命中）。
 *   2. 注释不扫（注释里出现技术词是允许的，例如「索引状态展示映射」）。
 *   3. JSX 属性值（`className=`、`id=`、`href=`）、路径/URL/存储键不扫。
 *   4. 词匹配用词边界（`(?<![A-Za-z0-9_])term(?![A-Za-z0-9_])`），
 *      因此 `segment` 不会命中 `segment_count`，`score` 不会命中 `score_threshold`。
 *   5. 错误码正则仅在含中文或空格的文案里启用，且排除「年/月/元/万/字/席位/号/页/条」上下文。
 */

import { readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { join, resolve, relative, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = resolve(HERE, '..');
const PRD_PATH = resolve(WEB_ROOT, '../../docs/prd-phase2-increment.md');

// ---------------------------------------------------------------------------
// 词表：由 PRD 第 7 章生成（--update-words 可重新生成；--check-words 可校验漂移）
// kind: sub   = 子串匹配（中文词，不会与 ASCII 标识符混淆）
//       word  = 词边界匹配（ASCII 词，天然排除 segment_count 这类标识符）
//       model = 模型品牌名（大小写不敏感，无豁免）
//       code  = 4-5 位错误码正则
// <BANNED_TERMS:BEGIN>
export const TERM_GROUPS = [
  { row: 1, kind: 'sub', term: "分块" },
  { row: 1, kind: 'sub', term: "切片" },
  { row: 1, kind: 'word', term: "chunk" },
  { row: 1, kind: 'word', term: "chunk size" },
  { row: 1, kind: 'word', term: "overlap" },
  { row: 2, kind: 'word', term: "segment" },
  { row: 2, kind: 'sub', term: "向量" },
  { row: 3, kind: 'sub', term: "索引" },
  { row: 3, kind: 'word', term: "indexing" },
  { row: 3, kind: 'sub', term: "入库" },
  { row: 3, kind: 'sub', term: "命中索引" },
  { row: 4, kind: 'sub', term: "重建索引" },
  { row: 4, kind: 'word', term: "reindex" },
  { row: 5, kind: 'word', term: "milvus" },
  { row: 5, kind: 'word', term: "embedding" },
  { row: 5, kind: 'sub', term: "向量化" },
  { row: 6, kind: 'word', term: "rag" },
  { row: 6, kind: 'sub', term: "召回" },
  { row: 6, kind: 'sub', term: "相似度" },
  { row: 6, kind: 'sub', term: "阈值" },
  { row: 6, kind: 'word', term: "top_k" },
  { row: 6, kind: 'word', term: "score" },
  { row: 7, kind: 'word', term: "token" },
  { row: 7, kind: 'word', term: "units" },
  { row: 7, kind: 'word', term: "prompt" },
  { row: 7, kind: 'word', term: "completion" },
  { row: 9, kind: 'word', term: "profile" },
  { row: 9, kind: 'sub', term: "模型" },
  { row: 9, kind: 'word', term: "temperature" },
  { row: 10, kind: 'word', term: "agent" },
  { row: 10, kind: 'sub', term: "智能体" },
  { row: 10, kind: 'sub', term: "编排" },
  { row: 10, kind: 'word', term: "workflow run" },
  { row: 11, kind: 'sub', term: "密钥" },
  { row: 11, kind: 'word', term: "api key" },
  { row: 11, kind: 'sub', term: "凭据" },
  { row: 11, kind: 'word', term: "app secret" },
  { row: 12, kind: 'word', term: "oauth" },
  { row: 12, kind: 'word', term: "access_token" },
  { row: 12, kind: 'sub', term: "回调地址" },
  { row: 12, kind: 'word', term: "redirect" },
  { row: 13, kind: 'sub', term: "租户" },
  { row: 13, kind: 'word', term: "tenant" },
  { row: 14, kind: 'sub', term: "审计" },
  { row: 14, kind: 'word', term: "audit log" },
  { row: 14, kind: 'sub', term: "事件流" },
  { row: 15, kind: 'word', term: "acl" },
  { row: 15, kind: 'sub', term: "权限位" },
  { row: 15, kind: 'word', term: "permission string" },
  { row: 15, kind: 'word', term: "rbac" },
  { row: 16, kind: 'word', term: "sku" },
  { row: 16, kind: 'sub', term: "计费周期" },
  { row: 16, kind: 'word', term: "proration" },
  { row: 18, kind: 'word', term: "seat" },
  { row: 18, kind: 'sub', term: "license 数" },
  { row: 19, kind: 'word', term: "top-k" },
  { row: 19, kind: 'sub', term: "记忆图谱" },
  { row: 19, kind: 'sub', term: "抽取器" },
];
// <BANNED_TERMS:END>

/** 模型品牌名（PRD 第 8 行「任何模型品牌名」的具体化；大小写不敏感） */
const MODEL_NAMES = ['gpt-4o', 'gpt-4', 'gpt-3.5', 'chatgpt', 'qwen', 'deepseek', 'claude', 'llama', '豆包', '文心一言', '通义千问'];

/** 错误码正则（PRD 第 17 行：任何 4-5 位数字码） */
const ERROR_CODE_RE = /\b[1-3]\d{4}\b/g;
/** 错误码豁免上下文：命中前后 6 字符内出现这些字，视为金额/日期/页码而非错误码 */
const CODE_EXEMPT_CTX = /[年月日号元万字的页条席位个次]/;

/** PRD 第 7 章「左列」允许的业务语言（用于自检正样本，避免把正确写法误判） */
const ALLOWED_SAMPLES = [
  '内容切分精细度', '内容片段', '文档处理进度', '重新处理', '知识库问答', '相关内容',
  '字数额度', '用量', '能力档位', '自动化任务', '企业', '操作记录', '成员席位', '已上传',
];

// ---------------------------------------------------------------------------
// 源码切分：注释 / 字符串 / 模板 / JSX 文本
// ---------------------------------------------------------------------------

/** 把注释替换为空格（保留换行与偏移，便于定位行号） */
function blankComments(src) {
  let out = '';
  let i = 0;
  while (i < src.length) {
    const c = src[i];
    const n = src[i + 1];
    if (c === '/' && n === '/') {
      while (i < src.length && src[i] !== '\n') { out += ' '; i += 1; }
    } else if (c === '/' && n === '*') {
      while (i < src.length && !(src[i] === '*' && src[i + 1] === '/')) {
        out += src[i] === '\n' ? '\n' : ' ';
        i += 1;
      }
      out += '  '; i += 2;
    } else if (c === '\'' || c === '"' || c === '`') {
      // 字符串整体保留（含引号），内部不视为注释
      const q = c; out += c; i += 1;
      while (i < src.length) {
        if (src[i] === '\\') { out += src[i] + (src[i + 1] ?? ''); i += 2; continue; }
        out += src[i];
        if (src[i] === q) { i += 1; break; }
        i += 1;
      }
    } else { out += c; i += 1; }
  }
  return out;
}

/** 去掉 import / export ... from 行（模块路径不是文案） */
function blankImports(src) {
  return src.replace(/^\s*(import|export)\b[^\n]*?from\s*['"][^'"]*['"]\s*;?\s*$/gm, (m) => ' '.repeat(m.length))
            .replace(/^\s*import\s*['"][^'"]*['"]\s*;?\s*$/gm, (m) => ' '.repeat(m.length));
}

/**
 * 取字符串字面量所属的 JSX 属性名（不在属性位置返回 null）。
 * 只有「非可见属性」会被豁免；`placeholder` / `title` / `alt` / `aria-label` 等
 * 属于用户可见文案，必须参与扫描（PRD 第 7 章：按钮、Toast、空态、提示、表头均计入）。
 */
const INVISIBLE_ATTRS = new Set([
  'classname', 'class', 'style', 'id', 'href', 'src', 'htmlfor', 'name',
  'role', 'type', 'key', 'onclick', 'onchange', 'onsubmit', 'd', 'viewbox',
  'fill', 'stroke', 'xmlns', 'width', 'height', 'target', 'rel',
]);

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
  // 回溯到最近的 '<' 或 '>'，若最近的是 '<' 说明在标签内部
  for (let m = k; m >= 0; m -= 1) {
    if (src[m] === '<') return attr;
    if (src[m] === '>' || src[m] === ';' || src[m] === '}') return null;
  }
  return null;
}

function isInvisibleAttr(attr) {
  return attr !== null && (INVISIBLE_ATTRS.has(attr) || attr.startsWith('data-'));
}

/** 抽取待检片段：字符串字面量 + 模板字面量 + JSX 文本节点 */
/**
 * 从一段代码片段里提取单/双引号字面量（用于 `${...}` 表达式内部的字符串）。
 * 模板里常见写法：`` `共 ${cond ? '正在建立索引' : ''} 条` ``，
 * 若只把 `${...}` 整体剔除，会把表达式里的文案一起丢掉 → 漏检。
 */
function extractQuotedStrings(chunk, baseOffset, out) {
  let i = 0;
  while (i < chunk.length) {
    const q = chunk[i];
    if (q === '\'' || q === '"') {
      const cs = i + 1;
      i += 1;
      while (i < chunk.length) {
        if (chunk[i] === '\\') { i += 2; continue; }
        if (chunk[i] === q) break;
        i += 1;
      }
      out.push({ kind: 'string', text: chunk.slice(cs, i), start: baseOffset + cs });
      i += 1;
      continue;
    }
    i += 1;
  }
}

function extractSegments(raw) {
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
        if (src[i] === '\\') { i += 2; continue; }
        if (src[i] === c) break;
        i += 1;
      }
      const text = src.slice(contentStart, i);
      if (!isInvisibleAttr(getJsxAttr(src, start))) segs.push({ kind: 'string', text, start: contentStart });
      i += 1;
      continue;
    }

    if (c === '`') {
      const contentStart = i + 1;
      i += 1;
      while (i < src.length) {
        if (src[i] === '\\') { i += 2; continue; }
        if (src[i] === '`') break;
        i += 1;
      }
      const inner = src.slice(contentStart, i);
      // 静态部分：${...} 是代码，替换为空格后只检静态文本
      segs.push({ kind: 'template', text: inner.replace(/\$\{[^}]*\}/g, ' '), start: contentStart });
      // 表达式内部：仍可能有字符串字面量文案（toast/ErrorState 里常见），逐个提取
      for (const m of inner.matchAll(/\$\{([^}]*)\}/g)) {
        extractQuotedStrings(m[1], contentStart + m.index + 2, segs);
      }
      i += 1;
      continue;
    }

    if (c === '>') {
      // JSX 文本节点：'>' 之后、'<' 或 '{' 之前；排除箭头函数/比较运算符
      const prev = src[i - 1];
      const next = src[i + 1];
      if (prev !== '=' && next !== '=' && next !== '>' && !/[)\]};]/.test(prev ?? '')) {
        const contentStart = i + 1;
        let j = contentStart;
        while (j < src.length && src[j] !== '<' && src[j] !== '{') j += 1;
        const text = src.slice(contentStart, j);
        const trimmed = text.trim();
        const looksLikeCode = /[;=&|?]/.test(trimmed) || trimmed.length === 0 || trimmed.length > 200;
        if (!looksLikeCode) segs.push({ kind: 'jsx-text', text, start: contentStart });
      }
    }
    i += 1;
  }
  return segs;
}

// ---------------------------------------------------------------------------
// 命中判定与豁免
// ---------------------------------------------------------------------------

const EXACT_ALLOW = new Set([
  'top_k', // 端点参数名，出现在 query key 里时不算文案（作为文案时仍会由顶层校验拦截）
]);

/** 判断 ASCII 字面量是否属于「键/路径/URL/类名」而非文案 */
function looksLikeKeyOrPath(text) {
  const t = text.trim();
  if (/[\u4e00-\u9fa5]/.test(t)) return false;
  if (/^https?:\/\//i.test(t)) return true;
  if (t.startsWith('/')) return true;                       // API 路径 / 前端路由（含模板拼接后）
  if (/\s/.test(t)) return false;
  if (/^[A-Za-z0-9_.:/@-]+$/.test(t) && /[_\-/.:@]/.test(t)) return true; // 存储键、字段名、类名、HTTP 头
  return false;
}

/** 判断该行是否处于类型声明上下文（联合类型字面量属于代码，不是文案） */
function isTypeContext(lineText) {
  if (!lineText) return false;
  if (/^\s*(export\s+)?(type|interface)\b/.test(lineText)) return true;
  if (/'\w+'\s*\|/.test(lineText) || /\|\s*'\w+'/.test(lineText)) return true;
  return false;
}

/** 字面量本身是否就是某个禁词（这类即使形如键名也要判命中，如 'access_token'） */
function isExactBannedTerm(text) {
  const t = text.trim().toLowerCase();
  if (MODEL_NAMES.includes(t)) return true;
  return TERM_GROUPS.some((g) => g.kind === 'word' && g.term.toLowerCase() === t);
}

function wordRegex(term) {
  const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`(?<![A-Za-z0-9_])${escaped}(?![A-Za-z0-9_])`, 'gi');
}

/** 在单个片段里找出所有命中 */
function findHits(text, kind, lineText = '') {
  const hits = [];

  // 1) 模型品牌名：最高优先级，任何出现都算
  for (const name of MODEL_NAMES) {
    const re = new RegExp(name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
    let m;
    while ((m = re.exec(text)) !== null) hits.push({ term: m[0], row: 8, via: '模型品牌名' });
  }

  const keyOrPath = (kind === 'string' || kind === 'template') && looksLikeKeyOrPath(text);
  const typeLiteral = isTypeContext(lineText) && /^[A-Za-z][A-Za-z0-9_]*$/.test(text.trim());

  // 类型声明里的联合字面量（如 'oauth2' | 'token' | 'none'）是代码，不参与判定
  if (typeLiteral) return hits;

  const exempt = keyOrPath;

  // 2) 词表（键/路径豁免；但字面量本身就是禁词时仍判命中）
  for (const { row, kind: tk, term } of TERM_GROUPS) {
    if (EXACT_ALLOW.has(text.trim()) && term === text.trim()) continue;
    if (exempt && !isExactBannedTerm(text)) continue;
    if (tk === 'sub') {
      let idx = text.indexOf(term);
      while (idx !== -1) {
        hits.push({ term, row, via: '子串' });
        idx = text.indexOf(term, idx + term.length);
      }
    } else {
      const re = wordRegex(term);
      let m;
      while ((m = re.exec(text)) !== null) hits.push({ term: m[0], row, via: '词边界' });
    }
  }

  // 3) 错误码：仅在「有中文或空格」的文案里启用，并做上下文豁免
  const isProse = /[\u4e00-\u9fa5]/.test(text) || /\s/.test(text);
  if (isProse && !exempt) {
    let m;
    ERROR_CODE_RE.lastIndex = 0;
    while ((m = ERROR_CODE_RE.exec(text)) !== null) {
      const before = text.slice(Math.max(0, m.index - 6), m.index);
      const after = text.slice(m.index + m[0].length, m.index + m[0].length + 6);
      if (CODE_EXEMPT_CTX.test(before) || CODE_EXEMPT_CTX.test(after)) continue;
      hits.push({ term: m[0], row: 17, via: '错误码' });
    }
  }

  return hits;
}

// ---------------------------------------------------------------------------
// 文件遍历与行号定位
// ---------------------------------------------------------------------------

function walk(dir, acc = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) walk(p, acc);
    // 跳过 `*.test.ts(x)` / `*.spec.ts(x)`：测试里的字符串是断言夹具与协议字段名
    // （如 `'access_token'`、`'openid profile'`），**不会成为用户可见文案**。
    // 本脚本的判据是「用户能否看到禁词」，测试文件不渲染任何界面，纳入扫描只会
    // 逼着测试写成 `'tok' + 'en'` 这类拼接去绕过——那是把门禁变成噪音。
    // （2026-10-30 补测第一批新增 93 个用例后实测暴露，此前测试恰好没有裸禁词字面量。）
    else if (
      /\.(ts|tsx)$/.test(name) &&
      !name.endsWith('.d.ts') &&
      !/\.(test|spec)\.(ts|tsx)$/.test(name)
    ) {
      acc.push(p);
    }
  }
  return acc;
}

function lineIndex(src) {
  const starts = [0];
  for (let i = 0; i < src.length; i += 1) if (src[i] === '\n') starts.push(i + 1);
  return (offset) => {
    let lo = 0; let hi = starts.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (starts[mid] <= offset) lo = mid; else hi = mid - 1;
    }
    return lo + 1;
  };
}

function scanFile(file) {
  const raw = readFileSync(file, 'utf8');
  const lines = raw.split('\n');
  const segs = extractSegments(raw);
  const lineOf = lineIndex(raw);
  const results = [];
  for (const seg of segs) {
    // 行号取「首个非空白字符」的位置：JSX 文本节点常以换行+缩进开头，
    // 直接用 start 会把 `>` 所在行当成命中行，导致报告行号偏小 1 行。
    const lead = Math.max(0, seg.text.search(/\S/));
    const offset = seg.start + (seg.text.search(/\S/) === -1 ? 0 : lead);
    const line = lineOf(offset);
    const lineText = lines[line - 1] ?? '';
    const hits = findHits(seg.text, seg.kind, lineText);
    if (!hits.length) continue;
    const snippet = seg.text.trim().replace(/\s+/g, ' ').slice(0, 80);
    for (const h of hits) {
      results.push({ file, line, term: h.term, row: h.row, via: h.via, kind: seg.kind, snippet });
    }
  }
  return results;
}

// ---------------------------------------------------------------------------
// 词表同步（--update-words / --check-words）
// ---------------------------------------------------------------------------

function parsePrdTerms() {
  const md = readFileSync(PRD_PATH, 'utf8');
  const start = md.indexOf('## 7. 术语对照表');
  const end = md.indexOf('\n## 8.', start);
  const section = md.slice(start, end === -1 ? undefined : end);
  const rows = section.split('\n').filter((l) => l.trim().startsWith('|'));
  const terms = [];
  for (const line of rows) {
    const cells = line.split('|').map((c) => c.trim());
    if (cells.length < 5) continue;
    const idx = Number(cells[1]);
    const banned = cells[4];
    if (!Number.isInteger(idx) || idx === 0) continue;
    if (/模型品牌名/.test(banned)) { terms.push({ row: idx, kind: 'model', term: '(模型品牌名)' }); continue; }
    if (/数字码/.test(banned)) { terms.push({ row: idx, kind: 'code', term: '(4-5位错误码)' }); continue; }
    for (const part of banned.split(/[、，,]/)) {
      const t = part.replace(/[`（）()]/g, '').trim();
      if (!t || /任何/.test(t)) continue;
      const ascii = /^[\x20-\x7F]+$/.test(t);
      terms.push({ row: idx, kind: ascii ? 'word' : 'sub', term: ascii ? t.toLowerCase() : t });
    }
  }
  // 同一词条可能在多行出现（如 chunk 在第 1、2 行，向量在第 2、5 行）：按 kind:term 去重，保留首次出现的行号
  const seen = new Set();
  const deduped = [];
  for (const t of terms) {
    const key = `${t.kind}:${t.term}`;
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(t);
  }
  return deduped;
}

function renderTerms(terms) {
  const body = terms
    .filter((t) => t.kind === 'sub' || t.kind === 'word')
    .map((t) => `  { row: ${t.row}, kind: '${t.kind}', term: ${JSON.stringify(t.term)} },`)
    .join('\n');
  return `// <BANNED_TERMS:BEGIN>\nexport const TERM_GROUPS = [\n${body}\n];\n// <BANNED_TERMS:END>`;
}

function currentTerms() {
  return TERM_GROUPS.map((t) => ({ row: t.row, kind: t.kind, term: t.kind === 'word' ? t.term.toLowerCase() : t.term }));
}

function updateWords() {
  const terms = parsePrdTerms();
  const file = fileURLToPath(import.meta.url);
  const src = readFileSync(file, 'utf8');
  const next = src.replace(/\/\/ <BANNED_TERMS:BEGIN>[\s\S]*?\/\/ <BANNED_TERMS:END>/, renderTerms(terms));
  writeFileSync(file, next, 'utf8');
  console.info(`词表已更新：${terms.filter((t) => t.kind === 'sub' || t.kind === 'word').length} 条（来源 ${PRD_PATH}）`);
  return 0;
}

function checkWords() {
  const prd = parsePrdTerms().filter((t) => t.kind === 'sub' || t.kind === 'word');
  const cur = currentTerms();
  // 比较「词面集合」而非行号：同一词在多行出现不影响判定
  const norm = (list) => [...new Set(list.map((t) => `${t.kind}:${t.term}`))].sort().join('|');
  if (norm(prd) === norm(cur)) {
    console.info(`词表与 PRD 一致（${new Set(cur.map((t) => `${t.kind}:${t.term}`)).size} 条词面）`);
    return 0;
  }
  console.error('词表与 PRD 漂移，请执行 --update-words');
  const a = new Set(cur.map((t) => `${t.kind}:${t.term}`));
  const b = new Set(prd.map((t) => `${t.kind}:${t.term}`));
  console.error('  仅脚本有：', [...a].filter((x) => !b.has(x)).join(', ') || '(无)');
  console.error('  仅 PRD 有：', [...b].filter((x) => !a.has(x)).join(', ') || '(无)');
  return 3;
}

// ---------------------------------------------------------------------------
// 自检（负样本必须命中、正样本必须不命中）
// ---------------------------------------------------------------------------

const NEGATIVE = [
  { text: '文档已上传，正在建立索引', term: '索引' },
  { text: '正在向量化，请稍候', term: '向量' },
  { text: '该企业租户已停用', term: '租户' },
  { text: '查看审计记录', term: '审计' },
  { text: '绑定密钥后可继续', term: '密钥' },
  { text: 'Token 已过期', term: 'token' },
  { text: '已召回 5 个片段', term: '召回' },
  { text: '相关内容相似度 92%', term: '相似度' },
  { text: '切换模型档位', term: '模型' },
  { text: 'Agent 任务执行中', term: 'agent' },
  { text: '升级 GPT-4o 版本', term: 'gpt-4o' },
  { text: '操作失败（错误码 20003），请重试', term: '20003' },
];

const POSITIVE = [
  '内容切分精细度：精细',
  '该内容片段来自员工手册',
  '文档处理进度：正在读取内容',
  '点击可重新处理该文档',
  '知识库问答已开启',
  '已用字数额度 12.5 万',
  '能力档位：通用助手',
  '自动化任务已完成',
];

/** 模板字符串专用样本：静态部分与 `${}` 表达式内部的文案都必须被抓到 */
const TEMPLATE_SAMPLES = [
  { src: 'const s = `共 ${n} 个索引`;', term: '索引', why: '静态部分含禁词' },
  { src: "const s = `${ok ? '正在建立索引' : ''}`;", term: '索引', why: '${} 表达式内部的字符串字面量' },
  { src: 'toast.error(`操作失败（错误码 20003），请重试`);', term: '错误码', why: '模板里直接写出的 4-5 位码' },
  { src: 'const s = `${a}${b}`;', term: '', why: '纯表达式拼接不应误报' },
  { src: 'toast.error(`请求失败（${err.code}），请重试`);', term: '', why: '${err.code} 是变量引用，不得当成错误码' },
];

function selfTest() {
  const failures = [];
  for (const s of NEGATIVE) {
    const hits = findHits(s.text, 'string');
    if (!hits.some((h) => h.term.toLowerCase() === s.term.toLowerCase())) {
      failures.push(`负样本未命中：「${s.text}」期望命中 ${s.term}`);
    }
  }
  for (const s of POSITIVE) {
    const hits = findHits(s, 'jsx-text');
    if (hits.length) failures.push(`正样本误报：「${s}」命中 ${hits.map((h) => h.term).join(',')}`);
  }
  const idSamples = ['segment_count', 'chunk_policy', 'index_status', 'segment_id', 'token_total'];
  for (const s of idSamples) {
    const hits = findHits(s, 'string');
    if (hits.length) failures.push(`标识符误报：${s} 命中 ${hits.map((h) => h.term).join(',')}`);
  }
  // 模板字符串提取自检（覆盖 `${}` 内部字面量这一易漏路径）
  for (const t of TEMPLATE_SAMPLES) {
    const segs = extractSegments(t.src);
    const hits = segs.flatMap((sg) => findHits(sg.text, sg.kind, t.src));
    if (t.term) {
      const ok = hits.some((h) => (t.term === '错误码' ? h.row === 17 : h.term === t.term));
      if (!ok) failures.push(`模板样本未命中（${t.why}）：${t.src}`);
    } else if (hits.length) {
      failures.push(`模板样本误报（${t.why}）：${t.src} 命中 ${hits.map((h) => h.term).join(',')}`);
    }
  }
  if (failures.length) {
    console.error(`SELF-TEST FAIL（${failures.length}）`);
    for (const f of failures) console.error('  -', f);
    return 0;
  }
  console.info(`SELF-TEST PASS：负样本 ${NEGATIVE.length}/${NEGATIVE.length} 命中；`
    + `正样本 ${POSITIVE.length} 条 0 误报；标识符 ${idSamples.length} 条 0 误报；`
    + `模板样本 ${TEMPLATE_SAMPLES.length} 条全部符合预期`);
  return 1;
}

// ---------------------------------------------------------------------------
// 构建产物扫描（--built，G-16）
//
// 源码扫描（默认模式）能拦住「写进代码里的禁词」，但拦不住「拼进产物的禁词」：
// 模板拼接、第三方组件文案、序列化进 RSC 载荷的字符串都可能绕过源码层判断。
// 因此在**真实产物**上再扫一遍：每个业务路由取预渲染 HTML 与服务端 bundle，
// 抽取可见文本后跑同一套词表 + 错误码正则。
// ---------------------------------------------------------------------------

const BUILT_APP_ROOT = resolve(WEB_ROOT, '.next/server/app');

/** 受检的业务路由（与 check-page-states.mjs 的页面清单保持一致，含运营后台） */
const BUILT_ROUTES = [
  'chat',
  'knowledge',
  'knowledge/[baseId]',
  'plugins',
  'plugins/[pluginId]',
  'workbench/[pluginId]',
  'workflows/[taskId]',
  'workflows',
  'team',
  'team/organization',
  'profile',
  'billing',
  'ops/tenants',
  'ops/tenants/[tenantId]',
  'ops/orders',
  'ops/metrics',
  'ops/settings',
];

/**
 * 从 `page_client-reference-manifest.js` 里解析该路由引用的客户端 chunk。
 *
 * 动态路由（`[id]`）没有预渲染 HTML，页面文案（含条件渲染文案）全在这些 chunk 里；
 * 只扫服务端 `page.js` 会得到「空壳」，导致禁词漏检。
 *
 * @param {string} manifestPath - 客户端引用清单路径
 * @returns {Array<string>} `static/chunks/xxx.js` 形式的相对路径
 */
export function clientChunkRefsOf(manifestPath) {
  try {
    const content = readFileSync(manifestPath, 'utf8');
    return [
      ...new Set(
        [...content.matchAll(/static\/chunks\/[A-Za-z0-9_.-]+\.js/g)].map((m) => m[0]),
      ),
    ];
  } catch {
    return [];
  }
}

/**
 * 校验每个受检路由都扫到了可见文本（空壳即红）。
 *
 * @param {Array<{route: string, textCount: number}>} summaries - 每路由的扫描统计
 * @returns {Array<string>} 问题列表
 */
export function checkBuiltCoverage(summaries) {
  const problems = [];
  for (const item of summaries) {
    if (item.textCount === 0) {
      problems.push(
        `路由 ${item.route} 未扫到任何可见文本（可能是空壳产物或客户端 chunk 未解析）`,
      );
    }
  }
  return problems;
}

/**
 * 收集某个路由的构建产物。
 *
 * 三类产物：
 * 1. 预渲染 HTML（静态路由才有）——首屏用户真正看到的文字；
 * 2. 该路由的服务端 bundle `page.js`——页面模块内的字符串常量；
 * 3. **HTML 引用到的客户端 chunk**——条件渲染的文案（错误态、提示条、抽屉）都在这里，
 *    只扫 HTML 会漏掉「只有出错才出现」的文案，等于给禁词留了后门。
 *
 * @param {string} route - 路由路径（不带前导斜杠）
 * @param {Set<string>} seenChunks - 已收录的 chunk（跨路由去重，避免重复扫描）
 * @returns {Array<{path: string, kind: 'html' | 'js'}>} 产物列表
 */
function builtArtifactsOf(route, seenChunks) {
  const found = [];
  const htmlPath = join(BUILT_APP_ROOT, `${route}.html`);

  let html = null;
  try {
    html = readFileSync(htmlPath, 'utf8');
    found.push({ path: htmlPath, kind: 'html' });
  } catch {
    // 动态路由没有预渲染 HTML
  }

  const routeBundles = [
    join(BUILT_APP_ROOT, '(main)', route, 'page.js'),
    join(BUILT_APP_ROOT, '(auth)', route, 'page.js'),
    join(BUILT_APP_ROOT, route, 'page.js'),
  ];
  for (const bundle of routeBundles) {
    try {
      if (statSync(bundle).isFile()) found.push({ path: bundle, kind: 'js' });
    } catch {
      // 不存在则跳过
    }
  }

  // HTML 里引用的客户端 chunk（`/_next/static/chunks/xxx.js`）
  if (html) {
    for (const match of html.matchAll(/(?:src|href)="\/_next\/(static\/[^"]+\.js)"/g)) {
      const relativePath = match[1];
      if (seenChunks.has(relativePath)) continue;
      seenChunks.add(relativePath);
      const chunkPath = join(WEB_ROOT, '.next', relativePath);
      try {
        if (statSync(chunkPath).isFile()) found.push({ path: chunkPath, kind: 'js' });
      } catch {
        // chunk 已被清理则跳过
      }
    }
  }

  // 动态路由没有预渲染 HTML：改从客户端引用清单里取 chunk（否则只会扫到空壳）
  const clientManifest = join(BUILT_APP_ROOT, '(main)', route, 'page_client-reference-manifest.js');
  for (const relativePath of clientChunkRefsOf(clientManifest)) {
    if (seenChunks.has(relativePath)) continue;
    seenChunks.add(relativePath);
    const chunkPath = join(WEB_ROOT, '.next', relativePath);
    try {
      if (statSync(chunkPath).isFile()) found.push({ path: chunkPath, kind: 'js' });
    } catch {
      // chunk 已被清理则跳过
    }
  }

  return found;
}

/**
 * 从 HTML 产物里抽取用户可见文本（文本节点 + 可见属性）。
 *
 * @param {string} html - HTML 内容
 * @returns {Array<string>} 可见文本片段（仅保留含中文的，避免 CSS/类名噪声）
 */
function extractHtmlVisibleTexts(html) {
  const texts = [];
  for (const match of html.matchAll(/>([^<>]{2,400})</g)) texts.push(match[1]);
  for (const match of html.matchAll(/(?:title|aria-label|placeholder|alt)="([^"]{2,400})"/g)) {
    texts.push(match[1]);
  }
  return texts.filter((text) => /[\u4e00-\u9fa5]/.test(text));
}

/**
 * 从一段代码里抽取单/双引号字面量（用于模板 `${}` 表达式内部）。
 *
 * @param {string} code - 代码片段
 * @returns {Array<string>} 字面量内容
 */
function collectSimpleStrings(code) {
  const found = [];
  let i = 0;
  while (i < code.length) {
    const quote = code[i];
    if (quote === '\'' || quote === '"') {
      const start = i + 1;
      i += 1;
      while (i < code.length) {
        if (code[i] === '\\') {
          i += 2;
          continue;
        }
        if (code[i] === quote) break;
        i += 1;
      }
      found.push(code.slice(start, i));
      i += 1;
      continue;
    }
    i += 1;
  }
  return found;
}

/**
 * 拆分模板字面量：静态文本 + `${}` 表达式列表。
 *
 * 必须把表达式剔出静态文本，否则 `${user.tenant.name}` 这类**变量名**
 * 会被当成文案命中（bundle 里全是压缩后的变量，误报会瞬间淹没真实问题）。
 *
 * @param {string} inner - 模板内部文本（不含反引号）
 * @returns {{staticText: string, expressions: Array<string>}} 拆分结果
 */
function splitTemplateLiteral(inner) {
  let staticText = '';
  const expressions = [];
  let i = 0;
  while (i < inner.length) {
    if (inner[i] === '\\') {
      staticText += inner[i] + (inner[i + 1] ?? '');
      i += 2;
      continue;
    }
    if (inner[i] === '$' && inner[i + 1] === '{') {
      let depth = 1;
      let j = i + 2;
      let expression = '';
      while (j < inner.length && depth > 0) {
        const ch = inner[j];
        if (ch === '{') depth += 1;
        else if (ch === '}') {
          depth -= 1;
          if (depth === 0) break;
        }
        expression += ch;
        j += 1;
      }
      expressions.push(expression);
      staticText += ' ';
      i = j + 1;
      continue;
    }
    staticText += inner[i];
    i += 1;
  }
  return { staticText, expressions };
}

/**
 * 从 JS 产物里抽取字符串字面量（含中文的才算「可见文案」）。
 *
 * 说明：
 * - bundle 里 ASCII 字面量绝大多数是字段名/类名/路径，把噪声算进来会淹没真实问题，
 *   因此只保留含中文的片段；
 * - 模板字面量按「静态文本 + `${}` 内字面量」两段提取，既不漏 `cond ? '已停用' : '正常'`，
 *   也不会把 `user.tenant.name` 这种变量名当文案。
 *
 * @param {string} js - JS 内容
 * @returns {Array<string>} 可见文本片段
 */
function extractJsVisibleTexts(js) {
  const texts = [];
  let i = 0;
  while (i < js.length) {
    const ch = js[i];
    if (ch === '\'' || ch === '"') {
      const start = i + 1;
      i += 1;
      while (i < js.length) {
        if (js[i] === '\\') {
          i += 2;
          continue;
        }
        if (js[i] === ch) break;
        i += 1;
      }
      const text = js.slice(start, i);
      if (/[\u4e00-\u9fa5]/.test(text)) texts.push(text);
      i += 1;
      continue;
    }
    if (ch === '`') {
      const start = i + 1;
      let j = start;
      while (j < js.length) {
        if (js[j] === '\\') {
          j += 2;
          continue;
        }
        if (js[j] === '`') break;
        j += 1;
      }
      const inner = js.slice(start, j);
      const { staticText, expressions } = splitTemplateLiteral(inner);
      if (/[\u4e00-\u9fa5]/.test(staticText)) texts.push(staticText);
      for (const expression of expressions) {
        for (const literal of collectSimpleStrings(expression)) {
          if (/[\u4e00-\u9fa5]/.test(literal)) texts.push(literal);
        }
      }
      i = j + 1;
      continue;
    }
    i += 1;
  }
  return texts;
}

/**
 * 构建产物扫描的自检：证明「产物提取 → 词表判定」这条链路真的抓得到东西。
 *
 * 只测抽取 + 判定，不依赖磁盘上的 .next（因此可在任何机器上跑）。
 *
 * @returns {number} 0 失败 / 1 通过（故意：通过=1）
 */
function builtSelfTest() {
  const failures = [];

  const htmlSample =
    '<div><p>文档已上传，正在建立索引</p><span title="升级 GPT-4o 版本"></span></div>';
  const htmlTexts = extractHtmlVisibleTexts(htmlSample);
  const htmlHits = htmlTexts.flatMap((text) => findHits(text, 'string', ''));
  if (!htmlHits.some((hit) => hit.term === '索引')) {
    failures.push('HTML 文本节点未抓到「索引」');
  }
  if (!htmlHits.some((hit) => hit.term.toLowerCase() === 'gpt-4o')) {
    failures.push('HTML 可见属性未抓到模型品牌名');
  }

  const jsSample = "const a='切换模型档位';const b='操作失败（错误码 20003）';const c='正常文案';";
  const jsTexts = extractJsVisibleTexts(jsSample);
  const jsHits = jsTexts.flatMap((text) => findHits(text, 'string', ''));
  if (!jsHits.some((hit) => hit.term === '模型')) {
    failures.push('JS 字符串未抓到「模型」');
  }
  if (!jsHits.some((hit) => hit.term === '20003')) {
    failures.push('JS 字符串未抓到 5 位错误码');
  }

  const cleanTexts = extractJsVisibleTexts("const a='该内容片段来自员工手册';");
  if (cleanTexts.flatMap((text) => findHits(text, 'string', '')).length > 0) {
    failures.push('正样本误报：合规文案被判为命中');
  }

  // 模板字面量：静态部分要抓，`${}` 里的变量名不能误报，`${}` 里的字面量也要抓
  const templateSample =
    'const a=`切换模型档位 ${g.tenant.plan}`;const b=`${ok?"正在建立索引":""}`;';
  const templateHits = extractJsVisibleTexts(templateSample).flatMap((text) =>
    findHits(text, 'string', ''),
  );
  if (!templateHits.some((hit) => hit.term === '模型')) {
    failures.push('模板静态部分未抓到「模型」');
  }
  if (!templateHits.some((hit) => hit.term === '索引')) {
    failures.push('模板 ${} 内部的字符串字面量未抓到「索引」');
  }
  if (templateHits.some((hit) => hit.term.toLowerCase() === 'tenant')) {
    failures.push('模板 ${} 里的变量名（tenant）被误判为文案');
  }

  // 动态路由只扫到空壳（0 条可见文本）必须判红
  const shellProblem = checkBuiltCoverage([{ route: '/workbench/[pluginId]', textCount: 0 }]);
  if (!shellProblem.some((item) => item.includes('未扫到任何可见文本'))) {
    failures.push('动态路由空壳未判红（漏扫未被发现）');
  }
  if (checkBuiltCoverage([{ route: '/chat', textCount: 12 }]).length > 0) {
    failures.push('正样本误报：有可见文本的路由被判为空壳');
  }

  // 纯 ASCII 片段不应被当成文案（否则 bundle 里的字段名会淹没真实问题）
  if (extractJsVisibleTexts("const k='segment_count';").length > 0) {
    failures.push('ASCII 标识符被误判为可见文案');
  }

  if (failures.length > 0) {
    console.error(`BUILT SELF-TEST FAIL（${failures.length}）`);
    for (const failure of failures) console.error('  -', failure);
    return 0;
  }

  console.info(
    'BUILT SELF-TEST PASS：HTML 文本/可见属性、JS 字符串（含模板静态段与 `${}` 内字面量）、动态路由空壳判红、' +
      '5 位错误码均能抓到；合规文案、ASCII 标识符与模板变量名 0 误报',
  );
  return 1;
}

/**
 * 扫描构建产物（--built 模式）。
 *
 * @param {boolean} json - 是否输出 JSON
 * @param {boolean} selfTestMode - 是否只跑抽取/判定自检
 * @returns {number} 退出码：0 无命中 / 1 有命中（自检模式下 1=通过）/ 2 产物缺失
 */
function scanBuilt(json, selfTestMode = false) {
  if (selfTestMode) return builtSelfTest();

  let htmlCount = 0;
  let jsCount = 0;
  let textCount = 0;
  const hits = [];
  const missingRoutes = [];
  const seenChunks = new Set();
  const routeSummaries = [];

  for (const route of BUILT_ROUTES) {
    const artifacts = builtArtifactsOf(route, seenChunks);
    if (artifacts.length === 0) {
      missingRoutes.push(route);
      continue;
    }
    let routeTextCount = 0;
    for (const artifact of artifacts) {
      let content;
      try {
        content = readFileSync(artifact.path, 'utf8');
      } catch {
        continue;
      }
      if (artifact.kind === 'html') htmlCount += 1;
      else jsCount += 1;

      const texts =
        artifact.kind === 'html' ? extractHtmlVisibleTexts(content) : extractJsVisibleTexts(content);
      textCount += texts.length;
      routeTextCount += texts.length;

      for (const text of texts) {
        for (const hit of findHits(text, 'string', '')) {
          hits.push({
            route,
            file: relative(WEB_ROOT, artifact.path),
            term: hit.term,
            row: hit.row,
            via: hit.via,
            snippet: text.trim().replace(/\s+/g, ' ').slice(0, 80),
          });
        }
      }
    }
    routeSummaries.push({ route, textCount: routeTextCount });
  }

  const coverageProblems = checkBuiltCoverage(routeSummaries);

  if (json) {
    console.info(
      JSON.stringify({ htmlCount, jsCount, textCount, missingRoutes, coverageProblems, hits }, null, 2),
    );
  } else {
    for (const hit of hits) {
      console.info(`${hit.file}:${hit.term}:${hit.snippet}`);
    }
    console.info(
      `[built] 扫描 ${htmlCount + jsCount} 个产物（HTML ${htmlCount} / JS ${jsCount}），` +
        `提取可见文本 ${textCount} 条，${hits.length} 命中`,
    );
    if (missingRoutes.length > 0) {
      console.info(`[built] 未找到以下路由的产物（请先 npm run build）：${missingRoutes.join(', ')}`);
    }
    for (const problem of coverageProblems) {
      console.info(`[built] ${problem}`);
    }
  }

  if (missingRoutes.length > 0) return 2;
  if (coverageProblems.length > 0) return 1;
  return hits.length > 0 ? 1 : 0;
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

function main(argv) {
  if (argv.includes('--update-words')) return updateWords();
  if (argv.includes('--check-words')) return checkWords();
  // --built 必须在 --self-test 之前判定：`--built --self-test` 走产物扫描器的自检
  if (argv.includes('--built')) return scanBuilt(argv.includes('--json'), argv.includes('--self-test'));
  if (argv.includes('--self-test')) return selfTest();

  // --dump：逐片段输出（kind + 行号 + 文本），用于核查提取逻辑是否漏扫
  if (argv.includes('--dump')) {
    const files = argv.filter((a) => !a.startsWith('--'));
    for (const f of files) {
      const abs = resolve(process.cwd(), f);
      const raw = readFileSync(abs, 'utf8');
      const lineOf = lineIndex(raw);
      const segs = extractSegments(raw);
      console.info(`\n=== ${f}（${segs.length} 片段）===`);
      for (const s of segs) {
        const idx = s.text.search(/\S/);
        const line = lineOf(s.start + (idx === -1 ? 0 : idx));
        const t = s.text.trim().replace(/\s+/g, ' ').slice(0, 100);
        // 纯 `${...}` 拼接（静态文本为空）也要打印，否则会误判为"漏扫"
        if (t) console.info(`  L${line}\t[${s.kind}]\t${t}`);
        else if (s.kind === 'template') console.info(`  L${line}\t[${s.kind}]\t(仅表达式拼接)`);
      }
    }
    return 0;
  }

  // --stats：打印实际扫到的片段数，用于自证「不是没扫到才 0 命中」
  const stats = argv.includes('--stats');
  if (stats) {
    const files = argv.filter((a) => !a.startsWith('--'));
    const targets = files.length ? files.map((f) => resolve(process.cwd(), f)) : walk(join(WEB_ROOT, 'src'));
    const acc = { string: 0, template: 0, 'jsx-text': 0 };
    let total = 0;
    for (const f of targets) {
      const segs = extractSegments(readFileSync(f, 'utf8'));
      total += segs.length;
      for (const s of segs) acc[s.kind] += 1;
    }
    console.info(`文件 ${targets.length} 个；待检片段 ${total} 个（字符串 ${acc.string} / 模板 ${acc.template} / JSX 文本 ${acc['jsx-text']}）`);
    return 0;
  }

  const json = argv.includes('--json');
  const files = argv.filter((a) => !a.startsWith('--'));
  const targets = files.length ? files.map((f) => resolve(process.cwd(), f)) : walk(join(WEB_ROOT, 'src'));

  let all = [];
  for (const f of targets) all = all.concat(scanFile(f));
  all.sort((a, b) => (a.file === b.file ? a.line - b.line : a.file < b.file ? -1 : 1));

  if (json) {
    console.info(JSON.stringify(all.map((h) => ({ ...h, file: relative(WEB_ROOT, h.file) })), null, 2));
  } else {
    for (const h of all) {
      console.info(`${relative(WEB_ROOT, h.file)}:${h.line}:${h.term}:${h.snippet}`);
    }
    if (all.length) {
      const byFile = new Map();
      for (const h of all) {
        const k = relative(WEB_ROOT, h.file);
        byFile.set(k, (byFile.get(k) || 0) + 1);
      }
      console.info(`\n合计 ${all.length} 处命中，涉及 ${byFile.size} 个文件`);
      for (const [k, v] of [...byFile].sort()) console.info(`  ${k}: ${v}`);
    } else {
      console.info(`扫描 ${targets.length} 个文件，0 命中`);
    }
  }
  return all.length ? 1 : 0;
}

process.exit(main(process.argv.slice(2)));
