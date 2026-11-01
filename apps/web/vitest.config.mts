import react from '@vitejs/plugin-react';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

/**
 * Vitest 配置（单元测试）。
 *
 * 只跑 `src/**\/*.test.ts(x)`；E2E 由 Playwright 负责。
 * 测试对象以纯函数为主（登录回跳、会话广播、表单合并/校验）；
 * 2026-11-01（补测第十二批）起对 Hook 状态机补测引入 React 官方渲染方式
 * （`react-dom/client` + `act`，见 `src/test/render-hook.tsx`）——只渲染
 * 返回 null 的探针组件，不渲染业务 UI，业务组件仍由 Playwright E2E 兜底。
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
      //
      // P1 补测第二批（2026-10-31，审计 M5 结转）：business-errors / error-guard /
      // capability-labels / memory-labels / utils / query-client 六个模块补测后实测：
      //   % Stmts **15.67** | % Branch 14.46 | % Funcs **8.25** | % Lines **15.73**
      //
      // P1 补测第三批（2026-10-31 H1）：api-client 令牌/请求头/查询参数/网关拦截/
      // 超时/各 API 组代表方法 + session-events 广播 27 项补测后实测：
      //   % Stmts **16.21** | % Branch 14.90 | % Funcs **9.31** | % Lines **16.32**
      //
      // P1 补测第四批（2026-10-31 H1 续）：QUERY_KEYS 键工厂 29 项 + api-client
      // toolApi/knowledgeExtApi/identityExtApi/opsApi/pluginsApi 补充 18 项后实测：
      //   % Stmts **17.56** | % Branch 15.36 | % Funcs **12.53** | % Lines **17.77**
      //
      // P1 补测第五批（2026-10-31 H1 续）：knowledgeApi/agentApi/memoryApi/
      // billingApi/identityApi/statsApi/capabilitiesApi 剩余方法 18 项后实测：
      //   % Stmts **17.94** | % Branch 15.59 | % Funcs **13.68** | % Lines **18.18**
      //
      // P1 补测第六批（2026-10-31 H1 续）：date-range-picker 纯函数 + progress
      // 纯函数 23 项后实测：
      //   % Stmts **18.94** | % Branch 16.24 | % Funcs **14.34** | % Lines **19.02**
      //
      // P1 补测第八批（2026-10-31 H1 续）：sidebar resolveNavItem 路由解析 +
      // session-gate/citation-drawer 常量 13 项后实测：
      //   % Stmts **20.08** | % Branch 16.67 | % Funcs **14.42** | % Lines **20.21**
      //
      // P1 补测第九批（2026-10-31 H1 续）：api-client pluginsApi 剩余方法 +
      // session-store 存储降级/形状校验/旧键迁移/过期判定 + format 边界 24 项后实测：
      //   % Stmts **20.21** | % Branch 16.70 | % Funcs **14.83** | % Lines **20.36**
      //
      // P1 补测第十批（2026-10-31 H1 续）：SSE 流分帧解析与降级 + upload 上传路径 +
      // 十个 API 组剩余方法 55 项后实测（api-client.ts 62.9% → **97.6%**）：
      //   % Stmts **22.60** | % Branch 17.42 | % Funcs **18.76** | % Lines **22.89**
      // 本批同时修复补测发现的 SSE 事件分发失配缺陷（handlerKeyFor 映射）。
      //
      // P1 补测第十一批（2026-11-01 H1 续）：use-conversation-store zustand 动作/
      // ID 合同/persist 口径 15 项 + QUERY_KEYS 剩余键工厂 14 个 + isIdempotentDuplicate/
      // redirect 协议段/oidc btoa 缺失尾分支后实测（use-api.ts 46.3% → 72.2%）：
      //   % Stmts **23.76** | % Branch 18.08 | % Funcs **21.23** | % Lines **24.05**
      //
      // P1 补测第十二批（2026-11-01 H1 续）：引入 React 渲染测试（renderHook
      // 基建 + react-dom/client + act），use-upload/use-retryable-request/
      // use-model-catalog/use-chat-stream/use-api 五个 Hook 状态机 44 项后实测：
      //   % Stmts **27.93** | % Branch 19.73 | % Funcs **25.08** | % Lines **28.23**
      // （use-api / use-upload / render-hook 均 100%）
      //
      // P1 补测第十三批（2026-11-01 H1 续）：工作流组件群渲染测试 31 项
      // （RunStatusBadge/TaskHeader/ChecklistPanel/StepTimeline/TaskMetricCards/
      // FailureActions）后实测：
      //   % Stmts **31.64** | % Branch 23.64 | % Funcs **29.41** | % Lines **32.05**
      // 本批同时修复补测发现的 Timeline 展开缺陷（非失败步骤点「详情」永远展不开，
      // 摘要与产物下载入口不可见）。
      //
      // P1 补测第十四批（2026-11-01 H1 续）：插件域（PluginCard 四态主按钮 /
      // CapabilityList G-16 能力 key 不进 DOM / ReviewList 三态）+ 记忆域
      // （MemoryCard 三口径 / MemoryEmpty 六态 / MemorySwitches 开关语义）
      // 组件渲染测试 35 项后实测：
      //   % Stmts **33.47** | % Branch 27.11 | % Funcs **31.20** | % Lines **34.01**
      //
      // P1 补测第十五批（2026-11-01 H1 续）：知识库组件（DocumentTable 三态/
      // 双空态/删除二次确认 + DocumentSearch 防抖四态）+ 组织架构组件
      // （DepartmentTree 选中/层级上限/拖拽移动）渲染测试 29 项后实测：
      //   % Stmts **38.40** | % Branch 31.76 | % Funcs **35.78** | % Lines **39.03**
      //
      // P1 补测第十六批（2026-11-01 H1 续）：计费域（PlanCompare 当前套餐/下架/
      // 权限与展示换算 + UsageCharts 空态/图表标注）+ 对话域（ConversationSidebar
      // 切换 + CitationDrawer 三种引用降级）+ ReviewForm 校验 23 项后实测：
      //   % Stmts **39.98** | % Branch 35.52 | % Funcs **37.99** | % Lines **40.65**
      // **H1 补测战役 40% 目标达成**（% Lines 40.65 ≥ 40）。
      //
      // P1 补测第十七批（2026-11-01 H1 续）：VersionHistory 升降级与回退二次确认 /
      // InstalledTable 双空态与工作台入口 / GrantPanel 批量授权 50 项拦截与撤销
      // 二次确认 22 项后实测：
      //   % Stmts **41.95** | % Branch 38.08 | % Funcs **40.11** | % Lines **42.72**
      //
      // P1 补测第十八批（2026-11-01 H1 续）：ConfirmOrderDialog 两步核销 /
      // OrderDrawer 三步下单 / BaseSettingsDialog 名称拦截与回填 22 项后实测：
      //   % Stmts **44.72** | % Branch 40.63 | % Funcs **42.07** | % Lines **45.67**
      // 四指标全部站上 40%。
      //
      // P1 补测第十九批（2026-11-01 H1 续）：GrantDrawer 安装/编辑双模式与必需
      // 能力约束 / RolePermissionDialog 权限矩阵只读态与只保存变化角色 17 项后实测：
      //   % Stmts **47.57** | % Branch 44.66 | % Funcs **44.93** | % Lines **48.49**
      //
      // P1 补测第二十批（2026-11-01 H1 续）：MobileNav 派生/溢出/ops 排除 /
      // GoalLauncher 防抖/命中理由/诚实动作 / ChatWindow 发送流 18 项后实测：
      //   % Stmts **50.32** | % Branch 46.92 | % Funcs **47.95** | % Lines **51.32**
      // **覆盖率突破 50%**（mobile-nav 100%）。
      // 棘轮继续只升不降，各留 ~0.3pp 余量：
      //   statements 50.0 / branches 46.6 / functions 47.6 / lines 51.0
      thresholds: {
        statements: 50.0,
        branches: 46.6,
        functions: 47.6,
        lines: 51.0,
      },
    },
  },
});
