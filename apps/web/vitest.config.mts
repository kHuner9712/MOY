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
      // 2026-10-29 全量分母实测（**注意列名，四列不可混用**）：
      //   % Stmts **5.82** | % Branch 5.73 | % Funcs **3.30** | % Lines **5.97**
      // 阈值 lines / functions 对应的是 `% Lines` 与 `% Funcs` 两列，
      // 不要把 `% Stmts` 当成 Lines 引用（对外文档写错列会被技术评审抓）。
      // （旧口径 44.08% → 新口径约 5.98%，比值与「分母 2882/23240 行」完全吻合，
      //  证实旧值确实是"假高"而非代码变差。阶段四补 /about 等页面后
      //  % Lines 5.98 → 5.97，**降的不是覆盖率，是分母变大了**。）
      // 阈值 = 实测基线向下留余量的**整改起点**，纪律 = 只升不降：
      // 每次补测试都把这个数往上抬，直到进入有意义的区间。
      //
      // P0（审计 2026-10-30 H2）：原地板 lines 5 / functions 2 贴着实测
      // （5.96 / 3.30）几乎不设防，按棘轮抬到四指标全覆盖：
      //   statements 5.5 / branches 5.5 / functions 3 / lines 5.5
      // （实测 5.82 / 5.73 / 3.30 / 5.96，各留 ~0.3pp 余量）。
      //
      // 40% 一步到位不可行（现状 5.8%，直接设 40% 门禁必红）——40% 是
      // 补测战役（P1）的目标值，优先补测清单（审计报告 H2）：
      // team/page.tsx（698 行）、plugins/[pluginId]/page.tsx（661 行）、
      // billing/page.tsx（458 行）、workbench/[pluginId]/page.tsx（421 行）、
      // oidc.ts（500 行）、session-store.ts（304 行）。
      //
      // P1 补测第一批（2026-10-30）：format.ts / oidc.ts / session-store.ts
      // 三个纯逻辑模块补测后全量分母实测（**注意列名，四列不可混用**）：
      //   % Stmts **13.44** | % Branch 12.88 | % Funcs **6.60** | % Lines **13.43**
      // 棘轮继续只升不降，各留 ~0.5pp 余量：
      //   statements 12.9 / branches 12.3 / functions 6.1 / lines 12.9
      thresholds: {
        statements: 12.9,
        branches: 12.3,
        functions: 6.1,
        lines: 12.9,
      },
    },
  },
});
