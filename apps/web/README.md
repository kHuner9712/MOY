# 麦子（MOY）· 前端交互层（apps/web）

Next.js 16 + React 19 前端。工程约束（技术黑盒化、错误文案口径、无技术术语门禁）
见仓库根 `docs/architecture-v1.0.md` 与 `README.md`。

## 单元测试（vitest）

```bash
npm run test --workspace apps/web            # 全量单测
npm run test --workspace apps/web -- --coverage  # 带覆盖率门禁
```

覆盖率门禁阈值在 `vitest.config.mts` 的 `coverage.thresholds.lines`：
初始值 44 = 2026-10-28 实测基线 44.08% 向下取整，**只升不降**（QA-04 纪律：
补测试只许抬高这个数；降低阈值等同拆门禁）。

## E2E（Playwright）

```bash
npm run test:e2e --workspace apps/web             # 全部 e2e
npx playwright test e2e/workflow.spec.ts          # 只跑贯穿旅程
```

- 浏览器用本机 Chrome（`channel: 'chrome'`），不下载 Playwright Chromium；
- dev server 由 `playwright.config.ts` 的 `webServer` 自动拉起（端口 3100）。

### 贯穿旅程（e2e/workflow.spec.ts）的运行前置

覆盖「统一登录 → 兑换会话 → 进入工作台 → 安装插件」主链路，**需要真实环境**：

1. 本地全量栈 healthy：`cd deploy && docker compose up -d`
   （compose 拓扑修复后各服务带 healthcheck，`docker compose ps` 全部 healthy 再跑）；
2. 提供统一登录测试账号（现场资产，不入库）：
   ```bash
   export OA_E2E_USERNAME=<测试用户名>
   export OA_E2E_PASSWORD=<测试口令>
   export OA_E2E_ADAPTER_BASE_URL=http://localhost:8000   # 可选，默认即此值
   ```

前置不满足时对应用例**自动 skip**（带原因），同文件的匿名前置段
（未登录访问工作台/插件页被带回登录页）零夹具、随时可跑。
e2e 暂不进 CI（CI 无浏览器环境，见 `ci-web.yml` 门禁 3 注释）。
