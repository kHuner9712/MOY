/**
 * 业务错误文案回归网（架构文档 3.0.3 / 9.2）。
 *
 * 铁律断言：前端只展示业务语言 message；code 缺失时按表兜底；
 * 未知码一律回落通用文案，**不得**把错误码拼进用户可见文本。
 */

import { describe, expect, it } from 'vitest';

import { BusinessError } from './api-client';
import {
  BUSINESS_ERROR_MESSAGES,
  BUSINESS_ERROR_SEGMENTS,
  CONTEXT_ERROR_MESSAGES,
  findMissingErrorCodes,
  isIdempotentDuplicate,
  isNotFound,
  messageOf,
  shouldReauthenticate,
  toBusinessMessage,
} from './business-errors';

describe('messageOf', () => {
  it('按错误码取兜底文案', () => {
    expect(messageOf(20002)).toBe('登录状态已失效，请重新登录');
    expect(messageOf(15006)).toContain('本月可用量已用完');
  });

  it('提供上下文时优先取同码多文案', () => {
    expect(messageOf(14006, 'department.level_exceeded')).toBe('组织层级最多 5 层，请调整后重试');
    expect(messageOf(20005, 'role.delete_referenced')).toBe('该角色下还有成员，请先调整成员角色');
  });

  it('上下文未命中时回落到该码默认文案', () => {
    expect(messageOf(14006, 'department.delete_non_empty')).toBe(
      '该部门下仍有成员或子部门，请先迁移后再删除',
    );
    // 该码存在上下文表但该上下文未登记（回落到该码默认文案）
    expect(messageOf(11006, 'department.level_exceeded')).toBe('文档正在处理，无法重复操作');
  });

  it('未知码/无码返回通用文案且不含数字码', () => {
    const text = messageOf(99999);
    expect(text).not.toContain('99999');
    expect(messageOf(0)).toBe(text);
  });
});

describe('toBusinessMessage', () => {
  it('BusinessError 有 message 时直接使用后端文案', () => {
    expect(toBusinessMessage(new BusinessError(15003, '席位已用满'))).toBe('席位已用满');
  });

  it('BusinessError message 为空时按 code + 上下文兜底', () => {
    expect(toBusinessMessage(new BusinessError(20003, ''))).toBe('当前账号没有该操作的权限');
    expect(toBusinessMessage(new BusinessError(20001, ''), 'grant.guest_editor')).toBe(
      '访客只能查看，不能上传或管理文档',
    );
  });

  it('普通 Error 使用其 message', () => {
    expect(toBusinessMessage(new Error('网络错误'))).toBe('网络错误');
  });

  it('非 Error 值回落通用文案', () => {
    expect(toBusinessMessage('boom')).toBe(messageOf(0));
    expect(toBusinessMessage(undefined)).toBe(messageOf(0));
  });
});

describe('错误分类', () => {
  it('20002 需要重新登录', () => {
    expect(shouldReauthenticate(new BusinessError(20002, ''))).toBe(true);
    expect(shouldReauthenticate(new BusinessError(20003, ''))).toBe(false);
    expect(shouldReauthenticate(new Error('x'))).toBe(false);
  });

  it('资源不存在类错误命中白名单', () => {
    for (const code of [20004, 11001, 12001, 14001, 14002, 14005]) {
      expect(isNotFound(new BusinessError(code, ''))).toBe(true);
    }
    expect(isNotFound(new BusinessError(20003, ''))).toBe(false);
  });

  it('幂等重复（20007）命中静默处理', () => {
    expect(isIdempotentDuplicate(new BusinessError(20007, ''))).toBe(true);
    expect(isIdempotentDuplicate(new BusinessError(20001, ''))).toBe(false);
    expect(isIdempotentDuplicate(new Error('x'))).toBe(false);
    expect(isIdempotentDuplicate(null)).toBe(false);
  });
});

describe('兜底表完整性', () => {
  it('findMissingErrorCodes 只报告未登记的码', () => {
    expect(findMissingErrorCodes([20001, 20002, 15006])).toEqual([]);
    expect(findMissingErrorCodes([20001, 99999])).toEqual([99999]);
  });

  it('分段区间上下界单调且覆盖主要域', () => {
    for (const [start, end] of Object.values(BUSINESS_ERROR_SEGMENTS)) {
      expect(start).toBeLessThan(end);
    }
    expect(Object.keys(BUSINESS_ERROR_SEGMENTS)).toEqual([
      'common',
      'model',
      'knowledge',
      'task',
      'tool',
      'identity',
      'billing',
      'plugin',
      'session',
      'system',
    ]);
  });

  it('上下文表只引用已登记的错误码', () => {
    for (const code of Object.keys(CONTEXT_ERROR_MESSAGES).map(Number)) {
      expect(BUSINESS_ERROR_MESSAGES[code]).toBeDefined();
    }
  });
});
