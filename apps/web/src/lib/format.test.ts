import { describe, expect, it } from 'vitest';

import {
  formatCents,
  formatDate,
  formatDateTime,
  formatDurationSeconds,
  formatFileSize,
  formatNumber,
  formatPercent,
  formatRemainingTime,
  formatUnits,
  formatUnitsExact,
  orPlaceholder,
  PLACEHOLDER,
} from './format';

/**
 * 展示层格式化的口径锁（架构文档 8.1「数据形态」）。
 *
 * 这些用例的价值不在「覆盖函数」，而在把三条铁律钉死：
 * 1. 金额后端给「分」→ 展示 `¥299.00`（两位小数 + 千分位 + 币种符号）；
 * 2. 额度后端给「字」→ ≥ 1 万走「万字」、< 1 万显示精确值；
 * 3. 时间本地时区 `YYYY-MM-DD HH:mm`，无值/非法值一律 `—`，
 *    **界面不得出现 null / undefined / 技术字段名**。
 *
 * 时间用例一律用**不带时区后缀**的 ISO 串（按本地时区解析），
 * 这样无论 CI 在哪个时区跑，期望值都稳定。
 */
describe('formatCents', () => {
  it('分 → 带币种符号与两位小数', () => {
    expect(formatCents(29900)).toBe('¥299.00');
    expect(formatCents(0)).toBe('¥0.00');
    expect(formatCents(5)).toBe('¥0.05');
  });

  it('整数千分位分组', () => {
    expect(formatCents(123456789)).toBe('¥1,234,567.89');
    expect(formatCents(100000)).toBe('¥1,000.00');
    expect(formatCents(999)).toBe('¥9.99');
  });

  it('负数保留负号且符号在数字之前', () => {
    expect(formatCents(-29900)).toBe('-¥299.00');
    expect(formatCents(-123456)).toBe('-¥1,234.56');
  });

  it('四舍五入到分', () => {
    expect(formatCents(1.4)).toBe('¥0.01');
    expect(formatCents(29900.6)).toBe('¥299.01');
    expect(formatCents(-1.6)).toBe('-¥0.02');
  });

  it('已登记币种用符号，未登记币种直接展示代码', () => {
    expect(formatCents(100, 'USD')).toBe('$1.00');
    expect(formatCents(100, 'EUR')).toBe('€1.00');
    expect(formatCents(100, 'GBP')).toBe('GBP 1.00');
  });

  it('非法数值回占位符', () => {
    expect(formatCents(Number.NaN)).toBe(PLACEHOLDER);
    expect(formatCents(Number.POSITIVE_INFINITY)).toBe(PLACEHOLDER);
    expect(formatCents('29900' as unknown as number)).toBe(PLACEHOLDER);
  });
});

describe('formatUnits', () => {
  it('不足 1 万显示精确值（带千分位）', () => {
    expect(formatUnits(8765)).toBe('8,765 字');
    expect(formatUnits(9999)).toBe('9,999 字');
    expect(formatUnits(0)).toBe('0 字');
  });

  it('满 1 万走「万字」口径，保留一位小数', () => {
    expect(formatUnits(10000)).toBe('1 万字');
    expect(formatUnits(1235000)).toBe('123.5 万字');
    expect(formatUnits(1234000)).toBe('123.4 万字');
    expect(formatUnits(12345678)).toBe('1234.6 万字');
  });

  it('一位小数的 .0 被去掉', () => {
    expect(formatUnits(10499)).toBe('1 万字');
    expect(formatUnits(200000)).toBe('20 万字');
  });

  it('负数保留负号', () => {
    expect(formatUnits(-5000)).toBe('-5,000 字');
    expect(formatUnits(-1235000)).toBe('-123.5 万字');
  });

  it('单位文案可替换', () => {
    expect(formatUnits(5000, '次')).toBe('5,000 次');
    expect(formatUnits(20000, '次')).toBe('2 万次');
  });

  it('非法数值回占位符', () => {
    expect(formatUnits(Number.NaN)).toBe(PLACEHOLDER);
    expect(formatUnits(Number.POSITIVE_INFINITY)).toBe(PLACEHOLDER);
  });
});

describe('formatUnitsExact', () => {
  it('不做万位换算，只加千分位', () => {
    expect(formatUnitsExact(1234567)).toBe('1,234,567');
    expect(formatUnitsExact(1234.6)).toBe('1,235');
    expect(formatUnitsExact(0)).toBe('0');
  });

  it('非法数值回占位符', () => {
    expect(formatUnitsExact(Number.NaN)).toBe(PLACEHOLDER);
  });
});

describe('formatFileSize', () => {
  it('字节取整显示', () => {
    expect(formatFileSize(0)).toBe('0 B');
    expect(formatFileSize(512)).toBe('512 B');
  });

  it('1024 进制逐级换算，保留一位小数', () => {
    expect(formatFileSize(1024)).toBe('1 KB');
    expect(formatFileSize(1536)).toBe('1.5 KB');
    expect(formatFileSize(1.2 * 1024 * 1024)).toBe('1.2 MB');
    expect(formatFileSize(1024 ** 3)).toBe('1 GB');
    expect(formatFileSize(1024 ** 4)).toBe('1 TB');
  });

  it('超过最大单位按 TB 封顶，不继续换算', () => {
    expect(formatFileSize(1024 ** 5)).toBe('1024 TB');
  });

  it('负数与非法数值回占位符', () => {
    expect(formatFileSize(-1)).toBe(PLACEHOLDER);
    expect(formatFileSize(Number.NaN)).toBe(PLACEHOLDER);
    expect(formatFileSize(Number.POSITIVE_INFINITY)).toBe(PLACEHOLDER);
  });
});

describe('formatDate / formatDateTime', () => {
  it('日期按本地时区格式化为 YYYY-MM-DD', () => {
    expect(formatDate('2026-03-05T12:34:56')).toBe('2026-03-05');
    expect(formatDate('2026-01-02T03:04:05')).toBe('2026-01-02');
  });

  it('日期时间为 YYYY-MM-DD HH:mm，月日时分补零', () => {
    expect(formatDateTime('2026-03-05T12:34:56')).toBe('2026-03-05 12:34');
    expect(formatDateTime('2026-01-02T03:04:05')).toBe('2026-01-02 03:04');
  });

  it('空值与非法时间串回占位符', () => {
    expect(formatDate('')).toBe(PLACEHOLDER);
    expect(formatDate('not-a-date')).toBe(PLACEHOLDER);
    expect(formatDateTime('')).toBe(PLACEHOLDER);
    expect(formatDateTime('not-a-date')).toBe(PLACEHOLDER);
  });
});

describe('formatPercent', () => {
  it('默认保留一位小数', () => {
    expect(formatPercent(82.53)).toBe('82.5%');
    expect(formatPercent(0)).toBe('0%');
    expect(formatPercent(100)).toBe('100%');
  });

  it('整数值去掉多余的 .0', () => {
    expect(formatPercent(82)).toBe('82%');
  });

  it('小数位数可控且被夹在 0~2', () => {
    expect(formatPercent(82.534, 2)).toBe('82.53%');
    expect(formatPercent(82, 2)).toBe('82%');
    expect(formatPercent(82.534, 5)).toBe('82.53%');
    expect(formatPercent(82.4, -3)).toBe('82%');
  });

  it('非法数值回占位符', () => {
    expect(formatPercent(Number.NaN)).toBe(PLACEHOLDER);
  });
});

describe('formatDurationSeconds', () => {
  it('不足 1 分钟只显示秒', () => {
    expect(formatDurationSeconds(0)).toBe('0 秒');
    expect(formatDurationSeconds(45.9)).toBe('45 秒');
    expect(formatDurationSeconds(59.99)).toBe('59 秒');
  });

  it('满 1 分钟显示「分 + 秒」', () => {
    expect(formatDurationSeconds(60)).toBe('1 分 0 秒');
    expect(formatDurationSeconds(183.4)).toBe('3 分 3 秒');
    expect(formatDurationSeconds(600)).toBe('10 分 0 秒');
  });

  it('负数与非法值回占位符', () => {
    expect(formatDurationSeconds(-1)).toBe(PLACEHOLDER);
    expect(formatDurationSeconds(Number.NaN)).toBe(PLACEHOLDER);
    expect(formatDurationSeconds(Number.POSITIVE_INFINITY)).toBe(PLACEHOLDER);
  });
});

describe('formatRemainingTime', () => {
  it('无法预估时提示「完成时间待确认」', () => {
    expect(formatRemainingTime(null)).toBe('完成时间待确认');
    expect(formatRemainingTime(Number.NaN)).toBe('完成时间待确认');
    expect(formatRemainingTime(Number.POSITIVE_INFINITY)).toBe('完成时间待确认');
  });

  it('≤ 0 提示「即将完成」', () => {
    expect(formatRemainingTime(0)).toBe('即将完成');
    expect(formatRemainingTime(-5)).toBe('即将完成');
  });

  it('不足 1 分钟提示「不到 1 分钟」', () => {
    expect(formatRemainingTime(30)).toBe('预计还需不到 1 分钟');
    expect(formatRemainingTime(59.9)).toBe('预计还需不到 1 分钟');
  });

  it('按分钟与小时两种口径切换', () => {
    expect(formatRemainingTime(60)).toBe('预计还需 1 分钟');
    expect(formatRemainingTime(120)).toBe('预计还需 2 分钟');
    expect(formatRemainingTime(3600)).toBe('预计还需 1 小时');
    expect(formatRemainingTime(5400)).toBe('预计还需 2 小时');
  });
});

describe('formatNumber', () => {
  it('整数千分位展示', () => {
    expect(formatNumber(1234)).toBe('1,234');
    expect(formatNumber(0)).toBe('0');
    expect(formatNumber(1234.56)).toBe('1,235');
  });

  it('负数保留负号', () => {
    expect(formatNumber(-1234567)).toBe('-1,234,567');
  });

  it('非法数值回占位符', () => {
    expect(formatNumber(Number.NaN)).toBe(PLACEHOLDER);
  });
});

describe('orPlaceholder', () => {
  it('文本去首尾空白后原样展示', () => {
    expect(orPlaceholder(' x ')).toBe('x');
    expect(orPlaceholder('麦子')).toBe('麦子');
  });

  it('空串 / 纯空白 / null / undefined 回占位符', () => {
    expect(orPlaceholder('')).toBe(PLACEHOLDER);
    expect(orPlaceholder('   ')).toBe(PLACEHOLDER);
    expect(orPlaceholder(null)).toBe(PLACEHOLDER);
    expect(orPlaceholder(undefined)).toBe(PLACEHOLDER);
  });

  it('兜底文案可替换', () => {
    expect(orPlaceholder('', '暂无')).toBe('暂无');
    expect(orPlaceholder(null, 'N/A')).toBe('N/A');
  });
});
