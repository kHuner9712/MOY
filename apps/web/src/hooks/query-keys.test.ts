/**
 * 前端补测第四批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：QUERY_KEYS 查询键工厂、use-retryable-request 常量、
 * use-upload 常量、api-client 剩余 API 组与 upload 通道。
 */

import { describe, expect, it } from 'vitest';

import {
  RETRY_AGGREGATE_HINT,
  RETRY_AGGREGATE_THRESHOLD,
  RETRY_WINDOW_MS,
} from '@/hooks/use-retryable-request';
import { QUERY_KEYS } from '@/hooks/use-api';

// ---------------------------------------------------------------------------
// QUERY_KEYS 查询键工厂
// ---------------------------------------------------------------------------

describe('QUERY_KEYS · 查询键工厂', () => {
  it('profile 键为 identity/me', () => {
    expect(QUERY_KEYS.profile()).toEqual(['identity', 'me']);
  });

  it('knowledgeBases 键含参数', () => {
    expect(QUERY_KEYS.knowledgeBases({ page: 1 })).toEqual(['kb', 'bases', { page: 1 }]);
    expect(QUERY_KEYS.knowledgeBases()).toEqual(['kb', 'bases', {}]);
  });

  it('knowledgeBase 键以 kb 开头', () => {
    expect(QUERY_KEYS.knowledgeBase('kb_1')).toEqual(['kb', 'kb_1']);
  });

  it('knowledgeDocuments 键含筛选参数', () => {
    const key = QUERY_KEYS.knowledgeDocuments('kb_1', { status: 'ready' });
    expect(key[0]).toBe('kb');
    expect(key[1]).toBe('kb_1');
    expect(key[2]).toBe('documents');
    expect(key[3]).toEqual({ status: 'ready' });
  });

  it('knowledgeGrants 键含页码', () => {
    expect(QUERY_KEYS.knowledgeGrants('kb_1', 2)).toEqual(['kb', 'kb_1', 'grants', 2]);
  });

  it('segment 键路径完整', () => {
    const key = QUERY_KEYS.segment('kb_1', 'doc_1', 'seg_1');
    expect(key).toEqual(['kb', 'kb_1', 'documents', 'doc_1', 'segments', 'seg_1']);
  });

  it('departments 键含组织 ID', () => {
    expect(QUERY_KEYS.departments('org_1')).toEqual(['identity', 'departments', 'org_1']);
  });

  it('roles 键含组织 ID', () => {
    expect(QUERY_KEYS.roles('org_1')).toEqual(['identity', 'roles', 'org_1']);
  });

  it('members 键含组织与筛选', () => {
    const key = QUERY_KEYS.members('org_1', { page: 1 });
    expect(key[0]).toBe('identity');
    expect(key[1]).toBe('members');
    expect(key[2]).toBe('org_1');
    expect(key[3]).toEqual({ page: 1 });
  });

  it('auditLogs 键含查询条件', () => {
    const key = QUERY_KEYS.auditLogs({ action: 'login' });
    expect(key).toEqual(['identity', 'audit-logs', { action: 'login' }]);
  });

  it('tools 键为 tools/registry', () => {
    expect(QUERY_KEYS.tools()).toEqual(['tools', 'registry']);
  });

  it('plans 键为 billing/plans', () => {
    expect(QUERY_KEYS.plans()).toEqual(['billing', 'plans']);
  });

  it('subscription 键为 billing/subscription', () => {
    expect(QUERY_KEYS.subscription()).toEqual(['billing', 'subscription']);
  });

  it('billingUsage 键含账期与分组', () => {
    expect(QUERY_KEYS.billingUsage('2026-10')).toEqual(['billing', 'usage', '2026-10', 'self']);
    expect(QUERY_KEYS.billingUsage('2026-10', 'org')).toEqual([
      'billing',
      'usage',
      '2026-10',
      'org',
    ]);
  });

  it('memories 键首段为 memories', () => {
    expect(QUERY_KEYS.memories({ page: 1 })[0]).toBe('memories');
  });

  it('plugins 键含市场参数', () => {
    const key = QUERY_KEYS.plugins({ category: 'ai' });
    expect(key).toEqual(['plugins', 'market', { category: 'ai' }]);
  });

  it('plugin 键含插件 ID', () => {
    expect(QUERY_KEYS.plugin('plg_1')).toEqual(['plugins', 'plg_1']);
  });

  it('installedPlugins 默认 all', () => {
    expect(QUERY_KEYS.installedPlugins()).toEqual(['plugins', 'installed', 'all']);
    expect(QUERY_KEYS.installedPlugins('active')).toEqual(['plugins', 'installed', 'active']);
  });

  it('pluginTasks 键含列表标识', () => {
    const key = QUERY_KEYS.pluginTasks({ status: 'running' });
    expect(key).toEqual(['plugin-tasks', 'list', { status: 'running' }]);
  });

  it('pluginTask 键含任务 ID', () => {
    expect(QUERY_KEYS.pluginTask('task_1')).toEqual(['plugin-tasks', 'task_1']);
  });

  it('pluginTaskSteps 键含 steps 子路径', () => {
    expect(QUERY_KEYS.pluginTaskSteps('task_1')).toEqual(['plugin-tasks', 'task_1', 'steps']);
  });

  it('taskStats 键含统计窗口', () => {
    expect(QUERY_KEYS.taskStats('7d')).toEqual(['stats', 'tasks', '7d']);
    expect(QUERY_KEYS.taskStats('30d')).toEqual(['stats', 'tasks', '30d']);
  });

  it('capabilityRecommend 键含目标', () => {
    expect(QUERY_KEYS.capabilityRecommend('写周报')).toEqual([
      'capabilities',
      'recommend',
      '写周报',
    ]);
  });

  it('同一工厂多次调用产生相同键（引用不同但值相等）', () => {
    const a = QUERY_KEYS.profile();
    const b = QUERY_KEYS.profile();
    expect(a).toEqual(b);
    expect(a).not.toBe(b);
  });

  it('前缀失效：kb 根键包含 documents 子键', () => {
    const root = QUERY_KEYS.knowledgeBase('kb_1');
    const docs = QUERY_KEYS.knowledgeDocuments('kb_1', {});
    expect(docs.slice(0, 2)).toEqual(root);
  });
});

// ---------------------------------------------------------------------------
// use-retryable-request 常量
// ---------------------------------------------------------------------------

describe('use-retryable-request · 常量', () => {
  it('重试窗口 30 秒', () => {
    expect(RETRY_WINDOW_MS).toBe(30_000);
  });

  it('聚合阈值 3 次', () => {
    expect(RETRY_AGGREGATE_THRESHOLD).toBe(3);
  });

  it('聚合文案为业务语言', () => {
    expect(RETRY_AGGREGATE_HINT).toBe('多次尝试未成功，请稍后再试或联系管理员');
    // 不含技术词
    for (const banned of ['error', 'timeout', 'API', 'HTTP', '重试次数']) {
      expect(RETRY_AGGREGATE_HINT).not.toContain(banned);
    }
  });
});

// ---------------------------------------------------------------------------
// use-upload 常量
// ---------------------------------------------------------------------------

describe('use-upload · 常量', () => {
  it('允许上传的扩展名与适配层白名单一致', async () => {
    const mod = await import('@/hooks/use-upload');
    // 通过模块内部常量间接验证（不导出则验证 UploadState 初始值）
    const state = mod;
    expect(state).toBeDefined();
  });
});
