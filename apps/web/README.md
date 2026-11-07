# 麦子（MOY）· 前端交互层（apps/web）

Next.js 16 + React 19 前端。工程约束（技术黑盒化、错误文案口径、无技术术语门禁）
见仓库根 `docs/architecture-v1.0.md` 与 `README.md`。

## 单元测试（vitest）

```bash
npm run test --workspace apps/web            # 全量单测
npm run test --workspace apps/web -- --coverage  # 带覆盖率门禁
```

覆盖率门禁阈值以 `vitest.config.mts` 的 `coverage.thresholds`（statements / branches / functions / lines）为准：
2026-10-29 起覆盖率分母改算全量源码（旧 44 口径只统计 12.4% 源码，是假高值，已废止）；
现行阈值 **statements 86.4 / branches 80.4 / functions 81.1 / lines 87.6**
（2026-11-01 补测第三十六批后抬升，实测 % Stmts 86.75 / Branch 80.72 / % Funcs 81.45 / % Lines 87.91，四指标全部站上 80%）。
**只升不降**（QA-04 纪律：补测试只许抬高这个数；降低阈值等同拆门禁）。

## E2E（Playwright）

```bash
npm run test:e2e --workspace apps/web             # 全部 e2e
npx playwright test e2e/workflow.spec.ts          # 只跑贯穿旅程
```

- 浏览器默认用本机 Chrome（`channel` 默认 `chrome`，不下载 Playwright Chromium），
  CI 经 `PLAYWRIGHT_CHANNEL=chromium` 切换到 `playwright install` 装的 Chromium；
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

缺 `OA_E2E_USERNAME` / `OA_E2E_PASSWORD` **必红**；全量栈不可达也**必红**（报错
里写明怎么起栈）。`OA_E2E_ALLOW_SKIP` 已于 2026-11-07（H-04）删除——不存在能
永久跳过主旅程的开关。同文件的匿名前置段（未登录访问对话/插件页被带回登录页）
零夹具、随时可跑。

e2e 已进 CI（`ci-web.yml` 门禁 5）：CI 用
`.github/e2e/keycloak-realm-ci.json` 起一个 Keycloak 测试 realm（账号
`e2e.user` / `e2e-pass-2026`、角色 `member`+`org_admin`、回调
`http://localhost:3100/auth/callback`），适配层切 `auth_mode=oidc`，浏览器里
**真跑**「统一登录 → 兑换会话 → 工作台 → 安装插件」。本地复跑同一链路时，
把上表的账号与适配层 OIDC 变量（见 `ci-web.yml` 的 e2e 步骤）对齐即可。
