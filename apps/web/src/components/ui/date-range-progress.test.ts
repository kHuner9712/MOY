/**
 * 前端补测第六批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：date-range-picker 纯函数、progress 纯函数、format 残余分支。
 */

import { describe, expect, it } from 'vitest';

import {
  DATE_RANGE_PRESETS,
  countDaysInRange,
  toIsoDate,
  toDateRangeQuery,
} from '@/components/ui/date-range-picker';
import { formatAmount, getProgressTone } from '@/components/ui/progress';

// ---------------------------------------------------------------------------
// date-range-picker 纯函数
// ---------------------------------------------------------------------------

describe('date-range-picker · toIsoDate', () => {
  it('输出 yyyy-MM-dd 格式', () => {
    expect(toIsoDate(new Date(2026, 9, 31))).toBe('2026-10-31');
  });

  it('个位数月份/日期补零', () => {
    expect(toIsoDate(new Date(2026, 0, 5))).toBe('2026-01-05');
  });
});

describe('date-range-picker · toDateRangeQuery', () => {
  it('空区间返回空对象', () => {
    expect(toDateRangeQuery({ start: '', end: '' })).toEqual({});
  });

  it('只填 start 时只返回 start_date', () => {
    expect(toDateRangeQuery({ start: '2026-10-01', end: '' })).toEqual({
      start_date: '2026-10-01',
    });
  });

  it('只填 end 时只返回 end_date', () => {
    expect(toDateRangeQuery({ start: '', end: '2026-10-31' })).toEqual({
      end_date: '2026-10-31',
    });
  });

  it('完整区间返回两个字段', () => {
    expect(toDateRangeQuery({ start: '2026-10-01', end: '2026-10-31' })).toEqual({
      start_date: '2026-10-01',
      end_date: '2026-10-31',
    });
  });
});

describe('date-range-picker · countDaysInRange', () => {
  it('同一天算 1 天', () => {
    expect(countDaysInRange({ start: '2026-10-31', end: '2026-10-31' })).toBe(1);
  });

  it('跨月区间正确计算', () => {
    expect(countDaysInRange({ start: '2026-10-01', end: '2026-11-01' })).toBe(32);
  });

  it('起止颠倒也返回正数', () => {
    expect(countDaysInRange({ start: '2026-11-01', end: '2026-10-01' })).toBe(32);
  });

  it('非法日期返回 0', () => {
    expect(countDaysInRange({ start: 'not-a-date', end: '2026-10-31' })).toBe(0);
    expect(countDaysInRange({ start: '2026-10-01', end: '' })).toBe(0);
  });
});

describe('date-range-picker · DATE_RANGE_PRESETS', () => {
  it('包含 6 个快捷区间', () => {
    expect(DATE_RANGE_PRESETS).toHaveLength(6);
  });

  it('每个预设都有 key/label/resolve', () => {
    for (const preset of DATE_RANGE_PRESETS) {
      expect(preset.key).toBeTruthy();
      expect(preset.label).toBeTruthy();
      expect(typeof preset.resolve).toBe('function');
    }
  });

  it('all 预设返回空区间', () => {
    const all = DATE_RANGE_PRESETS.find((p) => p.key === 'all');
    expect(all?.resolve(new Date())).toEqual({ start: '', end: '' });
  });

  it('last7 预设产生 7 天区间', () => {
    const last7 = DATE_RANGE_PRESETS.find((p) => p.key === 'last7');
    const today = new Date(2026, 9, 31);
    const range = last7!.resolve(today);
    expect(countDaysInRange(range)).toBe(7);
    expect(range.end).toBe('2026-10-31');
  });
});

// ---------------------------------------------------------------------------
// progress 纯函数
// ---------------------------------------------------------------------------

describe('progress · getProgressTone', () => {
  it('低于 80% 返回 primary', () => {
    expect(getProgressTone(0)).toBe('primary');
    expect(getProgressTone(50)).toBe('primary');
    expect(getProgressTone(79.9)).toBe('primary');
  });

  it('80%~99% 返回 warning', () => {
    expect(getProgressTone(80)).toBe('warning');
    expect(getProgressTone(95)).toBe('warning');
    expect(getProgressTone(99.9)).toBe('warning');
  });

  it('>=100% 返回 danger', () => {
    expect(getProgressTone(100)).toBe('danger');
    expect(getProgressTone(150)).toBe('danger');
  });

  it('非法值返回 primary', () => {
    expect(getProgressTone(Number.NaN)).toBe('primary');
    expect(getProgressTone(Number.POSITIVE_INFINITY)).toBe('primary');
  });
});

describe('progress · formatAmount', () => {
  it('小于 1 万按原值取整', () => {
    expect(formatAmount(860)).toBe('860');
    expect(formatAmount(9999)).toBe('9999');
  });

  it('大于等于 1 万按万展示', () => {
    expect(formatAmount(10000)).toBe('1 万');
    expect(formatAmount(12500)).toBe('1.25 万');
    expect(formatAmount(200000)).toBe('20 万');
  });

  it('负数取绝对值判断量级', () => {
    expect(formatAmount(-15000)).toBe('-1.5 万');
    expect(formatAmount(-500)).toBe('-500');
  });

  it('非法值返回 0', () => {
    expect(formatAmount(Number.NaN)).toBe('0');
    expect(formatAmount(Number.POSITIVE_INFINITY)).toBe('0');
  });

  it('小数四舍五入', () => {
    expect(formatAmount(12345.678)).toBe('1.23 万');
  });
});
