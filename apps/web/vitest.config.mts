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
      // R2-QA-03（审计 2026-10-29）：必须显式 `all: true` + `include`。
      //
      // v8 provider 默认 `all: false`，含义是「**只统计被测试 import 到的文件**」——
      // 于是覆盖率的分母由测试反向决定，没被任何测试碰过的页面根本不存在于分母里。
      // 2026-10-28 实测：报告只含 11 个文件 / 2882 行，而 `src` 下非测试源码有
      // 113 个文件 / 23240 行（分母仅 12.4%），25 个 page/layout 一个都没进去，
      // 于是「44%」对整站有效覆盖率只有约 5%。这是**假高**，比低覆盖率更危险：
      // 它让人以为有防线。
      all: true,
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/*.test.{ts,tsx}',
        'src/**/*.d.ts',
        // 纯类型声明文件没有可执行语句，计入分母会稀释指标
        'src/**/types.ts',
      ],
      // 分母改为全量源码后，真实值会显著低于旧阈值。**这是预期的**：
      // 旧 44 是分母只算 12.4% 源码时的产物，不是质量目标。
      //
      // 2026-10-29 全量分母实测：lines **5.98%** / functions **3.30%**
      // （旧口径 44.08% → 新口径 5.98%，比值与「分母 2882/23240 行」完全吻合，
      //  证实旧值确实是"假高"而非代码变差）。
      // 当前阈值取实测值向下留约 1pp 余量，作为**整改起点**：
      // 纪律 = 只升不降；每次补测试都把这个数往上抬，直到进入有意义的区间。
      thresholds: {
        lines: 5,
        functions: 2,
      },
    },
  },
});
