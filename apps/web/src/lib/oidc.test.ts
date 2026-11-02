/**
 * 回跳参数解析测试（P0-5 路线 A）。
 *
 * 解析层现在只剩一个职责：把地址栏 query 分成「可用 / 服务端拒绝 / 不是回跳」三类。
 * 它必须**只解析、不判断有效性**——`state` 与 PKCE verifier 现在都只存在于服务端的
 * httpOnly Cookie 里，前端既无从校验也不该假装校验。
 *
 * 判定顺序是安全约束（先看服务端拒绝、再看缺失、最后才看正常），因此这里逐条钉住。
 */

import { describe, expect, it } from 'vitest';

import { parseCallbackQuery } from './oidc';

describe('parseCallbackQuery', () => {
  it('code 与 state 齐全 → ok（含或不含问号均可）', () => {
    expect(parseCallbackQuery('?code=c_1&state=s_1')).toEqual({
      kind: 'ok',
      code: 'c_1',
      state: 's_1',
    });
    expect(parseCallbackQuery('code=c_1&state=s_1')).toEqual({
      kind: 'ok',
      code: 'c_1',
      state: 's_1',
    });
  });

  it('两侧空白被裁掉（避免把尾随空格带到兑换请求里）', () => {
    expect(parseCallbackQuery('?code=%20c_1%20&state=%20s_1%20')).toEqual({
      kind: 'ok',
      code: 'c_1',
      state: 's_1',
    });
  });

  it('服务端拒绝（带 error）→ provider-error，即使同时带着半截参数', () => {
    expect(parseCallbackQuery('?error=access_denied&code=c_1&state=s_1')).toEqual({
      kind: 'error',
      reason: 'provider-error',
    });
    expect(parseCallbackQuery('?error=')).toEqual({ kind: 'missing' });
  });

  it('两个参数都没有 → missing（用户是直接打开本页的）', () => {
    expect(parseCallbackQuery('')).toEqual({ kind: 'missing' });
    expect(parseCallbackQuery('?')).toEqual({ kind: 'missing' });
    expect(parseCallbackQuery('?foo=bar')).toEqual({ kind: 'missing' });
  });

  it('缺 state → missing-state（不发起兑换）', () => {
    expect(parseCallbackQuery('?code=c_1')).toEqual({ kind: 'error', reason: 'missing-state' });
    expect(parseCallbackQuery('?code=c_1&state=')).toEqual({
      kind: 'error',
      reason: 'missing-state',
    });
  });

  it('缺 code → missing-code（不发起兑换）', () => {
    expect(parseCallbackQuery('?state=s_1')).toEqual({ kind: 'error', reason: 'missing-code' });
    expect(parseCallbackQuery('?code=&state=s_1')).toEqual({
      kind: 'error',
      reason: 'missing-code',
    });
  });

  it('非字符串输入不抛异常（SSR / 异常调用兜底）', () => {
    expect(parseCallbackQuery(undefined as unknown as string)).toEqual({ kind: 'missing' });
    expect(parseCallbackQuery(null as unknown as string)).toEqual({ kind: 'missing' });
  });

  it('重复参数取第一个（与 URLSearchParams 语义一致，不做「最后一个为准」）', () => {
    expect(parseCallbackQuery('?code=a&code=b&state=s')).toEqual({
      kind: 'ok',
      code: 'a',
      state: 's',
    });
  });
});
