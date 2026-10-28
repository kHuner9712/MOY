import react from '@vitejs/plugin-react';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

/**
 * Vitest 配置（单元测试）。
 *
 * 只跑 `src/**\/*.test.ts(x)`；E2E 由 Playwright 负责。
 * 测试对象以纯函数为主（登录回跳、会话广播、表单合并/校验），
 * 因此不依赖 React 渲染器——monorepo 根目录另有一份 React 18，
 * 把渲染器拉进来会出现两套 React 混用。
 */
const webRoot = fileURLToPath(new URL('.', import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(webRoot, 'src'),
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
    restoreMocks: true,
    coverage: {
      provider: 'v8',
      // QA-04（审计 2026-10-28）：初始阈值 = 2026-10-28 实测基线 44.08% 向下取整。
      // 纪律：**只升不降**——补测试只许抬高这个数；降低阈值等同拆门禁。
      thresholds: {
        lines: 44,
      },
    },
  },
});
