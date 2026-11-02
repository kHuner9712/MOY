/**
 * 「一次用户动作」幂等键持有器回归网（审计 Q-11 / S-09 / P1-12）。
 *
 * 只测纯函数：页面层（`order-drawer` / `team`）负责把返回值塞进 ref，
 * 真正决定「这次点击算不算新动作」的判定全在这里，所以这里要测穿。
 */

import { describe, expect, it } from 'vitest';

import { buildActionFingerprint, resolveActionIdempotencyKey } from './idempotency';

/** UUID v4 的严格格式 */
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe('resolveActionIdempotencyKey', () => {
  it('首次动作生成新键并记下指纹', () => {
    const action = resolveActionIdempotencyKey(null, 'pro|1|online|new');
    expect(action.key).toMatch(UUID_V4);
    expect(action.fingerprint).toBe('pro|1|online|new');
  });

  it('指纹不变时复用同一个键对象（双击提交 / 失败重试）', () => {
    const first = resolveActionIdempotencyKey(null, 'pro|1|online|new');
    const second = resolveActionIdempotencyKey(first, 'pro|1|online|new');
    expect(second).toBe(first);
  });

  it('指纹变化时换新键（改了席位 / 支付方式就是另一次动作）', () => {
    const first = resolveActionIdempotencyKey(null, 'pro|1|online|new');
    const second = resolveActionIdempotencyKey(first, 'pro|3|online|new');
    expect(second.key).not.toBe(first.key);
    expect(second.fingerprint).toBe('pro|3|online|new');
  });

  it('持有者键为空串时重新生成（防止把空头发给后端）', () => {
    const broken = { key: '', fingerprint: 'pro|1|online|new' };
    const action = resolveActionIdempotencyKey(broken, 'pro|1|online|new');
    expect(action.key).toMatch(UUID_V4);
    expect(action.key).not.toBe('');
  });
});

describe('buildActionFingerprint', () => {
  it('按顺序拼接，null/undefined 记为空段', () => {
    expect(buildActionFingerprint(['pro', 3, 'online', 'new'])).toBe('pro|3|online|new');
    expect(buildActionFingerprint(['pro', null, undefined, 0])).toBe('pro|||0');
  });

  it('分隔符被转义，不会把两次不同动作拼成同一指纹', () => {
    const left = buildActionFingerprint(['a|b', 'c']);
    const right = buildActionFingerprint(['a', 'b|c']);
    expect(left).not.toBe(right);
    expect(left).toBe('a\\|b|c');
    expect(right).toBe('a|b\\|c');
  });

  it('空数组得到空串', () => {
    expect(buildActionFingerprint([])).toBe('');
  });
});
