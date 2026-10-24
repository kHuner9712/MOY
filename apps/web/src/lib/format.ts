/**
 * 展示层格式化工具（架构文档 8.1「数据形态」裁决）。
 *
 * 铁律：
 * - 金额后端给「分」，展示层统一 `formatCents()` → `¥299.00`；
 * - 额度后端给「字数」，展示层统一 `formatUnits()` → `123.5 万字`（< 1 万显示精确值）；
 * - 时间一律本地时区 `YYYY-MM-DD HH:mm`，无值时统一显示 `—`（不显示 null / undefined）。
 *
 * 本文件全部是**纯函数**，不含任何请求逻辑，可在服务端组件与客户端组件共用。
 */

/** 占位符：字段为空或数值非法时统一展示 */
export const PLACEHOLDER = '—';

/** 币种符号表（未登记币种直接展示币种代码） */
const CURRENCY_SYMBOLS: Readonly<Record<string, string>> = Object.freeze({
  CNY: '¥',
  USD: '$',
  EUR: '€',
});

/** 万位换算基数 */
const TEN_THOUSAND = 10_000;

/** 文件大小单位阶梯（按 1024 进制） */
const FILE_SIZE_UNITS: readonly string[] = ['B', 'KB', 'MB', 'GB', 'TB'];

/**
 * 判断数值是否可用于展示。
 *
 * @param value - 待判断的数值
 * @returns 有限数字返回 true
 */
function isFiniteNumber(value: number): boolean {
  return typeof value === 'number' && Number.isFinite(value);
}

/**
 * 给整数部分加千分位。
 *
 * @param integerText - 只含数字的整数部分文本
 * @returns 形如 `1,234,567` 的文本
 */
function groupThousands(integerText: string): string {
  const negative = integerText.startsWith('-');
  const digits = negative ? integerText.slice(1) : integerText;
  let grouped = '';
  for (let index = 0; index < digits.length; index += 1) {
    const fromEnd = digits.length - index;
    grouped += digits[index] ?? '';
    if (fromEnd > 1 && fromEnd % 3 === 1) {
      grouped += ',';
    }
  }
  return negative ? `-${grouped}` : grouped;
}

/**
 * 金额展示：分 → 带币种符号与千分位的两位小数。
 *
 * @param amountCents - 金额（分），如 `29900`
 * @param currency - 币种代码，默认 `CNY`
 * @returns 形如 `¥299.00` 的文本；非法数值返回 `—`
 */
export function formatCents(amountCents: number, currency = 'CNY'): string {
  if (!isFiniteNumber(amountCents)) {
    return PLACEHOLDER;
  }
  const negative = amountCents < 0;
  const absolute = Math.abs(Math.round(amountCents));
  const yuan = Math.floor(absolute / 100);
  const cents = absolute % 100;
  const symbol = CURRENCY_SYMBOLS[currency] ?? `${currency} `;
  const body = `${groupThousands(String(yuan))}.${String(cents).padStart(2, '0')}`;
  return `${negative ? '-' : ''}${symbol}${body}`;
}

/**
 * 额度展示：字数额度 → 「万字」口径。
 *
 * 口径（PRD 5.4）：≥ 1 万显示一位小数的「万字」；< 1 万显示精确值。
 *
 * @param units - 额度（字）
 * @param unitLabel - 额度单位文案，默认「字」
 * @returns 形如 `123.5 万字` / `8,765 字` 的文本；非法数值返回 `—`
 */
export function formatUnits(units: number, unitLabel = '字'): string {
  if (!isFiniteNumber(units)) {
    return PLACEHOLDER;
  }
  const absolute = Math.abs(Math.round(units));
  const sign = units < 0 ? '-' : '';
  if (absolute < TEN_THOUSAND) {
    return `${sign}${groupThousands(String(absolute))} ${unitLabel}`;
  }
  const inTenThousand = absolute / TEN_THOUSAND;
  // 四舍五入到一位小数；`toFixed` 后去掉多余的 `.0`
  const rounded = (Math.round(inTenThousand * 10) / 10).toFixed(1).replace(/\.0$/, '');
  return `${sign}${rounded} 万${unitLabel}`;
}

/**
 * 额度精确值展示（用于「已用 / 总量」并排场景，不做万位换算）。
 *
 * @param units - 额度（字）
 * @returns 形如 `1,234,567` 的文本
 */
export function formatUnitsExact(units: number): string {
  if (!isFiniteNumber(units)) {
    return PLACEHOLDER;
  }
  return groupThousands(String(Math.round(units)));
}

/**
 * 文件大小展示（1024 进制，保留一位小数）。
 *
 * @param bytes - 字节数
 * @returns 形如 `1.2 MB` 的文本；非法数值返回 `—`
 */
export function formatFileSize(bytes: number): string {
  if (!isFiniteNumber(bytes) || bytes < 0) {
    return PLACEHOLDER;
  }
  let size = bytes;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < FILE_SIZE_UNITS.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  const unit = FILE_SIZE_UNITS[unitIndex] ?? 'B';
  if (unitIndex === 0) {
    return `${Math.round(size)} ${unit}`;
  }
  const rounded = (Math.round(size * 10) / 10).toFixed(1).replace(/\.0$/, '');
  return `${rounded} ${unit}`;
}

/**
 * 把 ISO 时间串解析成 Date。
 *
 * @param iso - ISO 8601 时间串；空串返回 null
 * @returns Date 或 null
 */
function parseIso(iso: string): Date | null {
  if (!iso) {
    return null;
  }
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * 两位补零。
 *
 * @param value - 数字
 * @returns 至少两位的字符串
 */
function pad2(value: number): string {
  return String(value).padStart(2, '0');
}

/**
 * 日期展示：`YYYY-MM-DD`（本地时区）。
 *
 * @param iso - ISO 8601 时间串
 * @returns 日期文本；空值返回 `—`
 */
export function formatDate(iso: string): string {
  const date = parseIso(iso);
  if (!date) {
    return PLACEHOLDER;
  }
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

/**
 * 日期时间展示：`YYYY-MM-DD HH:mm`（本地时区）。
 *
 * @param iso - ISO 8601 时间串
 * @returns 日期时间文本；空值返回 `—`
 */
export function formatDateTime(iso: string): string {
  const date = parseIso(iso);
  if (!date) {
    return PLACEHOLDER;
  }
  const day = `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
  return `${day} ${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
}

/**
 * 百分比展示：保留一位小数，不带多余的 `.0`。
 *
 * @param value - 百分比数值（`82.53` 表示 82.53%）
 * @param fractionDigits - 小数位数，默认 1
 * @returns 形如 `82.5%` 的文本
 */
export function formatPercent(value: number, fractionDigits = 1): string {
  if (!isFiniteNumber(value)) {
    return PLACEHOLDER;
  }
  const safeDigits = Math.min(Math.max(Math.trunc(fractionDigits), 0), 2);
  const rounded = value.toFixed(safeDigits);
  return `${safeDigits > 0 ? rounded.replace(/\.0+$/, '') : rounded}%`;
}

/**
 * 业务语言时长展示：秒 → 「N 秒」/「N 分 M 秒」（W1 切片 A，M3 口径）。
 *
 * 口径：不足 1 分钟只显示秒；满 1 分钟显示「分 + 秒」；0 显示「0 秒」；
 * 非法值回占位符（界面不出现技术字段名）。
 *
 * @param seconds - 时长（秒），如 `183.4`
 * @returns 形如 `3 分 3 秒` / `45 秒` / `0 秒` 的文本
 */
export function formatDurationSeconds(seconds: number): string {
  if (!isFiniteNumber(seconds) || seconds < 0) {
    return PLACEHOLDER;
  }
  const totalSeconds = Math.floor(seconds);
  if (totalSeconds < 60) {
    return `${totalSeconds} 秒`;
  }
  const minutes = Math.floor(totalSeconds / 60);
  const restSeconds = totalSeconds % 60;
  return `${minutes} 分 ${restSeconds} 秒`;
}

/**
 * 预估剩余时间展示（业务语言，不出现技术口径）。
 *
 * @param seconds - 预估剩余秒数；无法预估时为 null
 * @returns 形如 `预计还需 2 分钟` 的文本
 */
export function formatRemainingTime(seconds: number | null): string {
  if (seconds === null || !isFiniteNumber(seconds)) {
    return '完成时间待确认';
  }
  if (seconds <= 0) {
    return '即将完成';
  }
  if (seconds < 60) {
    return '预计还需不到 1 分钟';
  }
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) {
    return `预计还需 ${minutes} 分钟`;
  }
  const hours = Math.round(minutes / 60);
  return `预计还需 ${hours} 小时`;
}

/**
 * 整数千分位展示。
 *
 * @param value - 数值
 * @returns 形如 `1,234` 的文本
 */
export function formatNumber(value: number): string {
  if (!isFiniteNumber(value)) {
    return PLACEHOLDER;
  }
  return groupThousands(String(Math.round(value)));
}

/**
 * 字符串空值兜底。
 *
 * @param value - 原始文本
 * @param fallback - 为空时的兜底文案，默认 `—`
 * @returns 展示文本
 */
export function orPlaceholder(value: string | null | undefined, fallback = PLACEHOLDER): string {
  const trimmed = typeof value === 'string' ? value.trim() : '';
  return trimmed ? trimmed : fallback;
}
