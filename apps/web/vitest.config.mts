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
      //
      // P1 补测第二十一批（2026-11-01 H1 续）：page 组件层（关于页版本/开源
      // 许可证清单/合规入口双分支 + 个人中心页会话兜底与用量三态 + 对话主页
      // 额度提醒阈值 + 我的记忆页六套空态/删除二次确认/删除全部关键字合同/
      // 删空回退页码）27 项后实测（about 页 100%、profile 页语句/行 100%、
      // chat 页分支/行 100%、memories 页 87.6%）：
      //   % Stmts **53.74** | % Branch 50.68 | % Funcs 51.38 | % Lines 54.73
      // **四指标全部站上 50%**。棘轮继续只升不降，各留 ~0.3pp 余量：
      //   statements 53.4 / branches 50.3 / functions 51.0 / lines 54.4
      //
      // P1 补测第二十二批（2026-11-01 H1 续）：列表型 page（工作流列表页
      // 表格三态/四维筛选参数拼装/插件名映射 + 知识库列表页卡片四类徽标/
      // 新建弹窗名称校验与 snake_case 参数口径）15 项后实测
      // （workflows 页 100%、knowledge 页 95.7%）：
      //   % Stmts **56.10** | % Branch 52.91 | % Funcs 53.67 | % Lines 57.21
      // 棘轮继续只升不降，各留 ~0.3pp 余量：
      //   statements 55.8 / branches 52.6 / functions 53.3 / lines 56.9
      //
      // P1 补测第二十三批（2026-11-01 H1 续）：套餐与账单页编排（额度 80% 阈值
      // 提醒 / 订阅摘要与待生效切换 / 「选套餐」三分流——免费新开、付费升级、
      // 更便宜降级二次确认 / 订单与账单三态）16 项 + chat 页空体回归 1 项后实测
      // （billing 页 90% 行覆盖）。本批同时修复补测发现的用量空体崩页缺陷
      //（billing/chat 页 quotaWarning 只判 `!== undefined`，null 空体取
      // `usage_percent` 直接白屏，「还没有用量记录」空态永远走不到）：
      //   % Stmts **57.42** | % Branch 55.66 | % Funcs 55.14 | % Lines 58.51
      // 棘轮继续只升不降，各留 ~0.3pp 余量：
      //   statements 57.1 / branches 55.3 / functions 54.8 / lines 58.2
      //
      // P1 补测第二十四批（2026-11-01 H1 续）：运营域四页（平台统计指标卡口径
      // 与区间切换 / 订单核销仅限待支付 / 系统配置表单校验与保存直更缓存 /
      // 企业停用恢复二次确认）21 项后实测：
      //   % Stmts **60.60** | % Branch 59.19 | % Funcs 58.33 | % Lines 61.74
      // **四指标全部站上 60%**（% Lines 50 → 60 用时 4 批）。
      // 本批沉淀一条测试经验：依赖 effect 二次提交的链路（如配置页表单回填）
      // 在长 act 内轮询观察不到、在 act 外轮询会报警，需「act 内逐拍放行」
      // 模式（见 ops-pages.test.tsx 的 waitForSettingsForm）。
      // 棘轮继续只升不降，各留 ~0.3pp 余量：
      //   statements 60.3 / branches 58.8 / functions 58.0 / lines 61.4
      //
      // P1 补测第二十五批（2026-11-01 H1 续）：插件详情页编排（三态与 16002
      // 下架专门文案 / 四个主按钮态与套餐拦截 / 授权抽屉 install-edit 分流 /
      // 卸载二次确认保留口径 / 配置草稿初始化与保存 / 评价校验与分页 /
      // action=install 自动开抽屉）18 项后实测（该页行覆盖 90%）：
      //   % Stmts **63.72** | % Branch 62.59 | % Funcs 61.84 | % Lines 64.88
      // 棘轮继续只升不降，各留 ~0.3pp 余量：
      //   statements 63.4 / branches 62.3 / functions 61.5 / lines 64.6
      //
      // P1 补测第二十六批（2026-11-01 H1 续）：认证域登录闭环（登录页生产/
      // 开发双构建分支与「先存后跳」PKCE 流程 + 回跳流程校验失败不碰存储/
      // 落库失败明确失败/异常收敛统一文案 + 根落地页重定向）18 项后实测
      // （login 页 92.7%、callback-flow 95.5%）：
      //   % Stmts **66.30** | % Branch 64.13 | % Funcs 62.99 | % Lines 67.59
      // 棘轮继续只升不降，各留 ~0.3pp 余量：
      //   statements 66.0 / branches 63.8 / functions 62.6 / lines 67.3
      //
      // P1 补测第二十七批（2026-11-01 H1 续）：团队管理页四标签（成员角色
      // 即时保存/移出二次确认知识库转交口径/邀请校验与失败文案 + 组织架构
      // 入口 + 插件能力开关合成六项与只提交企业能力 + 操作记录懒加载/
      // 目录筛选/双空态）20 项后实测（team 页行覆盖 94.5%）：
      //   % Stmts **70.18** | % Branch 67.89 | % Funcs 66.74 | % Lines 71.58
      // **% Lines 突破 70%**。本批把 act 等待模式收敛为「act 窗口内睡眠 +
      // 窗口外断言」的逐拍循环（waitForText）：长 act 内轮询看不见提交、
      // vi.waitFor 的睡眠区间又在 act 外刷警告，此写法两者皆免。
      // 棘轮继续只升不降，各留 ~0.3pp 余量：
      //   statements 69.8 / branches 67.5 / functions 66.4 / lines 71.2
      //
      // P1 补测第二十八批（2026-11-01 H1 续）：两个详情页（知识库详情三态/
      // 权限锁定/上传本地校验/删除双确认 + 任务详情 12001-20003 特殊错误/
      // waiting_confirm 三条动作/SSE 节流合并刷新/进行中才开兜底轮询）
      // 18 项后实测（知识库详情 84%、任务详情 87% 行覆盖）：
      //   % Stmts **74.38** | % Branch 70.45 | % Funcs 70.34 | % Lines 75.82
      // **四指标全部站上 70%**。棘轮继续只升不降，各留 ~0.3pp 余量：
      //   statements 74.0 / branches 70.1 / functions 70.0 / lines 75.5
      //
      // P1 补测第二十九批（2026-11-01 H1 续）：岗位工作台宿主（buildSections
      // 声明驱动直测 / 16003 未添加专门空态 / 必填拦截与 goal 深链下发 /
      // 排队文案三档 / waiting_confirm 三条动作 / 进行中才轮询进终态停并刷
      // 最近执行 / 步骤与产物区块）14 项后实测（workbench 页行覆盖 90.7%）：
      //   % Stmts **78.47** | % Branch 74.28 | % Funcs 73.77 | % Lines 79.57
      // 本批再沉淀一条假定时器口径：`advanceTimersByTimeAsync` 必须切短窗
      // 逐拍推进——一个长 act 窗口里查询落定的提交压到窗口退出才落盘，
      // 数据滞后会让「进终态停轮询」这类状态机断言看起来失灵（非产品缺陷）。
      // 棘轮继续只升不降，各留 ~0.3pp 余量：
      //   statements 78.1 / branches 73.9 / functions 73.4 / lines 79.2
      thresholds: {
        statements: 78.1,
        branches: 73.9,
        functions: 73.4,
        lines: 79.2,
      },
    },
  },
});
