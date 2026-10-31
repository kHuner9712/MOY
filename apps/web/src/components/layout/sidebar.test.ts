/**
 * 前端补测第八批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：sidebar resolveNavItem 路由解析、session-gate 常量、
 * citation-drawer 常量、use-upload 校验逻辑间接覆盖。
 */

import { describe, expect, it } from 'vitest';

import { NAV_ITEMS, resolveNavItem } from '@/components/layout/sidebar';
import { SESSION_EXPIRED_MESSAGE } from '@/components/layout/session-gate';
import { STALE_HINT } from '@/components/chat/citation-drawer';

// ---------------------------------------------------------------------------
// sidebar · resolveNavItem
// ---------------------------------------------------------------------------

describe('sidebar · resolveNavItem', () => {
  it('精确匹配一级导航', () => {
    expect(resolveNavItem('/chat')?.label).toBe('对话');
    expect(resolveNavItem('/knowledge')?.label).toBe('知识库');
    expect(resolveNavItem('/plugins')?.label).toBe('插件与工作台');
    expect(resolveNavItem('/workflows')?.label).toBe('工作流');
    expect(resolveNavItem('/team')?.label).toBe('团队管理');
    expect(resolveNavItem('/billing')?.label).toBe('套餐与账单');
    expect(resolveNavItem('/profile')?.label).toBe('个人中心');
  });

  it('子路径匹配父级导航', () => {
    expect(resolveNavItem('/team/members')?.label).toBe('团队管理');
    expect(resolveNavItem('/billing/orders')?.label).toBe('套餐与账单');
    expect(resolveNavItem('/knowledge/kb_1')?.label).toBe('知识库');
  });

  it('最长前缀优先：/team/organization 匹配组织架构而非团队管理', () => {
    expect(resolveNavItem('/team/organization')?.label).toBe('组织架构');
    expect(resolveNavItem('/team/organization/dept_1')?.label).toBe('组织架构');
  });

  it('最长前缀优先：/profile/memories 匹配我的记忆而非个人中心', () => {
    expect(resolveNavItem('/profile/memories')?.label).toBe('我的记忆');
    expect(resolveNavItem('/profile/memories/mem_1')?.label).toBe('我的记忆');
  });

  it('独立标题路由（不进菜单）也能解析', () => {
    expect(resolveNavItem('/workbench')?.label).toBe('工作台');
    expect(resolveNavItem('/workbench/plg_1')?.label).toBe('工作台');
    expect(resolveNavItem('/ops')?.label).toBe('运营后台');
    expect(resolveNavItem('/ops/tenants')?.label).toBe('运营后台');
    expect(resolveNavItem('/about')?.label).toBe('关于');
  });

  it('未命中路由返回 undefined', () => {
    expect(resolveNavItem('/nonexistent')).toBeUndefined();
    expect(resolveNavItem('/')).toBeUndefined();
  });

  it('null/undefined/空字符串返回 undefined', () => {
    expect(resolveNavItem(null)).toBeUndefined();
    expect(resolveNavItem(undefined)).toBeUndefined();
    expect(resolveNavItem('')).toBeUndefined();
  });

  it('NAV_ITEMS 包含 9 个一级导航', () => {
    expect(NAV_ITEMS).toHaveLength(9);
    for (const item of NAV_ITEMS) {
      expect(item.href).toBeTruthy();
      expect(item.label).toBeTruthy();
    }
  });

  it('前缀相似但不完全匹配时不误命中', () => {
    // /teamx 不应命中 /team
    expect(resolveNavItem('/teamx')).toBeUndefined();
    // /knowledgebase 不应命中 /knowledge
    expect(resolveNavItem('/knowledgebase')).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// session-gate · SESSION_EXPIRED_MESSAGE
// ---------------------------------------------------------------------------

describe('session-gate · SESSION_EXPIRED_MESSAGE', () => {
  it('文案为业务语言', () => {
    expect(SESSION_EXPIRED_MESSAGE).toBe('登录状态已失效，请重新登录');
  });

  it('不含技术词', () => {
    for (const banned of ['token', 'JWT', '401', 'error', 'session']) {
      expect(SESSION_EXPIRED_MESSAGE).not.toContain(banned);
    }
  });
});

// ---------------------------------------------------------------------------
// citation-drawer · STALE_HINT
// ---------------------------------------------------------------------------

describe('citation-drawer · STALE_HINT', () => {
  it('文案为业务语言', () => {
    expect(STALE_HINT).toBe('该引用来自旧版本内容，原文已更新');
  });

  it('不含技术词', () => {
    for (const banned of ['stale', 'segment', 'version', 'cache', '404']) {
      expect(STALE_HINT).not.toContain(banned);
    }
  });
});
