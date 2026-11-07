import { expect, test, type Page } from '@playwright/test';

/**
 * 贯穿旅程 e2e（QA-04，审计 2026-10-28）：
 * 统一登录 → 兑换会话 → 进入工作台 → 安装一个插件。
 *
 * 与 session-redirect.spec.ts（零夹具冒烟）互补：那条锁「未登录会被带回登录页」，
 * 这条锁「登录后的主链路真的走得通」。三个环节任意一个退化都会直接挡用户：
 * - 统一登录跳转/回跳参数构造坏了 → 兑换永远失败（callback 停在错误提示）；
 * - 兑换成功但会话没落库 → 进工作台又被弹回登录页（死循环）；
 * - 工作台进得去但安装链路断 → 插件页点「查看并安装」无响应或报技术错误。
 *
 * 运行前置（缺任一项都**红**，没有豁免开关）：
 * 1. 本地全量栈 healthy：`docker compose -f deploy/docker-compose.yml up -d`
 *    （QA-04 依赖批次 C 的 compose 修复成果，先修拓扑再跑 e2e）；
 * 2. web dev server：`npm run dev --workspace apps/web`（playwright.config 的
 *    webServer 会自动拉起，端口 3100）；
 * 3. 环境变量提供真实测试账号：
 *    `OA_E2E_USERNAME` / `OA_E2E_PASSWORD`（统一登录侧的测试用户）。
 *
 * 本地跑法：`npx playwright test e2e/workflow.spec.ts`（详见 apps/web/README.md）。
 * CI 跑法：`.github/workflows/ci-web.yml` 的 `e2e-gate` 用 `quay.io/keycloak/keycloak:26.8.1`
 * 起 `.github/e2e/keycloak-realm-ci.json` 声明的测试 realm，账号就来自该文件。
 */

/** 统一登录测试账号。CI 由 `.github/e2e/keycloak-realm-ci.json` 提供，本地由开发者显式注入 */
const E2E_USERNAME = process.env.OA_E2E_USERNAME ?? '';
const E2E_PASSWORD = process.env.OA_E2E_PASSWORD ?? '';

/** 适配层地址（默认 compose 对外端口；与 NEXT_PUBLIC_ADAPTER_BASE_URL 的本地默认一致） */
const ADAPTER_BASE = process.env.OA_E2E_ADAPTER_BASE_URL ?? 'http://localhost:8000';

test.beforeEach(async ({ request }) => {
  // 前置探测失败即红：早期这里用 test.skip，结果"全量栈没起"和"链路真的通了"
  // 在报告里都是绿的。现在失败信息写清怎么起栈，避免用 skip 掩盖主旅程。
  const health = await request.get(`${ADAPTER_BASE}/healthz`).catch(() => null);
  if (health === null || !health.ok()) {
    throw new Error(
      `适配层不可达（${ADAPTER_BASE}/healthz）。贯穿旅程的前置不成立，` +
        '先起栈（deploy/docker-compose.yml 或 CI 的 e2e-gate 步骤）再跑。',
    );
  }
});

const HAS_E2E_CREDS = Boolean(E2E_USERNAME && E2E_PASSWORD);

/**
 * 缺凭据 → 一条**必然失败**的用例（R2-QA-02 的结论保留，豁免开关已删除）。
 *
 * 2026-11-07（H-04）：`OA_E2E_ALLOW_SKIP` 让主旅程可以被永久跳过——这不是"本地便利"，
 * 而是把唯一一条浏览器级主链路回归防线变成可选项。CI 现在起 Keycloak 测试 realm
 * 并注入真实账号；本地缺账号时请自己起栈注入，不再提供静默豁免。
 */
test.describe('贯穿旅程（需真实测试账号）', () => {
  if (!HAS_E2E_CREDS) {
    test('缺少 OA_E2E_USERNAME / OA_E2E_PASSWORD（缺失即失败，见 R2-QA-02）', () => {
      throw new Error(
        '未设置 OA_E2E_USERNAME / OA_E2E_PASSWORD，「登录 → 兑换 → 工作台 → 插件」主链路无法验证。' +
          '这不是环境噪音：跳过它等于让主链路没有回归防线。' +
          'CI 由 .github/e2e/keycloak-realm-ci.json + ci-web.yml 的 keycloak 步骤提供账号。',
      );
    });
  } else {
    test('统一登录 → 兑换会话 → 进入工作台 → 安装插件', async ({ page }) => {
      // ① 登录页：统一登录入口存在且诚实展示
      await page.goto('/login');
      await expect(page.getByRole('button', { name: '统一登录' })).toBeVisible();

      // ② 点击统一登录 → 跳出应用到身份服务授权页
      await page.getByRole('button', { name: '统一登录' }).click();
      await expect(page).toHaveURL(/protocol\/openid-connect\/auth/);

      // ③ 在身份服务完成登录（realm 页面由 Keycloak 渲染，锚定表单而非文案）
      await page.locator('#username').fill(E2E_USERNAME);
      await page.locator('#password').fill(E2E_PASSWORD);
      await page.getByRole('button', { name: /Sign In|登录/ }).click();

      // ④ 回跳兑换：callback 页消费 code 换会话，成功后离开 /auth/callback
      await expect(page).not.toHaveURL(/\/auth\/callback/, { timeout: 20_000 });

      // ⑤ 会话生效：进入主应用（/chat 为登录默认落点）不再被弹回登录页；
      // 「工作台」是 /workbench/[pluginId] 动态路由，裸 /workbench 本就是 404，
      // 故主链路落点断言用 /chat（工作台经插件安装完成后进入，见 ⑥ 之后）。
      await page.goto('/chat');
      await expect(page).not.toHaveURL(/\/login/);
      // 主应用外壳就位（侧栏 + 主导航）；旧断言找的是首页大标题，工作台改版后
      // 该文案不再出现在 /chat，改用导航地标，语义仍是"真的进了主应用"。
      await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible();

      // ⑥ 插件旅程：进入插件页，安装第一个可安装插件
      await page.goto('/plugins');
      await expect(page).not.toHaveURL(/\/login/);
      const installButton = page.getByRole('button', { name: '查看并安装' }).first();
      await expect(installButton).toBeVisible();
      await installButton.click();
      // 安装抽屉打开（授权配置面板）即证明安装链路入口可达；真实提交依赖底座
      // 插件运行时，抽屉内提交留给插件域专项用例（grant-drawer 单测已覆盖逻辑）。
      await expect(page.getByRole('dialog').or(page.getByText('能力授权'))).toBeVisible();
    }); // ← 结束 test('统一登录 → …')
  } // ← 结束 else if (HAS_E2E_CREDS)
});

test.describe('贯穿旅程的匿名前置段（零夹具，随时可跑）', () => {
  // 刻意放在同一文件：贯穿旅程 skip 时，本段保证该 spec 在 CI/本地仍有
  // 确定性断言在跑，文件不沦为"整段 skip 的摆设"。

  /**
   * 替 `/sso/session` 探针作答「从未登录」四元组（夹具订正 2026-11-02；断言不动）。
   *
   * 依据与 session-redirect.spec.ts 同源：令牌链路改造后「未登录」只能由探针的
   * 回答判定（设计 §2.4.7），而 dev 服务器无 `/sso/*` 路由、stub 形态下五个端点
   * 恒 503「统一登录未启用」（`sso_routes.py` 硬约束 2）——探针必然失败，而探针
   * 失败按设计停错误态、不跳登录（`session-gate.tsx` 头注释）。故在 HTTP 边界给
   * 出设计契约的输入（与 `SsoSessionData` 逐字段对齐），被测的仍是浏览器内真实
   * 的「会话门 → 三态判据 → buildLoginHref」链路，断言与改造前逐字相同。
   */
  async function answerNeverLoggedIn(page: Page): Promise<void> {
    await page.route('**/sso/session', (route) =>
      route.fulfill({
        json: {
          code: 0,
          message: '',
          data: { authenticated: false, expires_in: null, refreshable: false, expired: false },
        },
      }),
    );
  }

  async function expectLoginGate(page: Page, path: string): Promise<void> {
    await answerNeverLoggedIn(page);
    await page.goto(path);
    await expect(page).toHaveURL(/\/login\?next=/);
  }

  test('未登录访问对话页被带回登录页', async ({ page }) => {
    await expectLoginGate(page, '/chat');
  });

  test('未登录访问插件页被带回登录页', async ({ page }) => {
    await expectLoginGate(page, '/plugins');
  });
});
