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
 * 运行前置（缺一自动 skip，理由写在 skip 信息里——不让它假装通过）：
 * 1. 本地全量栈 healthy：`docker compose -f deploy/docker-compose.yml up -d`
 *    （QA-04 依赖批次 C 的 compose 修复成果，先修拓扑再跑 e2e）；
 * 2. web dev server：`npm run dev --workspace apps/web`（playwright.config 的
 *    webServer 会自动拉起，端口 3100）；
 * 3. 环境变量提供真实测试账号：
 *    `OA_E2E_USERNAME` / `OA_E2E_PASSWORD`（统一登录侧的测试用户）。
 *
 * 本地跑法：`npx playwright test e2e/workflow.spec.ts`（详见 apps/web/README.md）。
 */

/** 统一登录测试账号（不设则跳过贯穿旅程——账号属现场资产，不入库） */
const E2E_USERNAME = process.env.OA_E2E_USERNAME ?? '';
const E2E_PASSWORD = process.env.OA_E2E_PASSWORD ?? '';

/** 适配层地址（默认 compose 对外端口；与 NEXT_PUBLIC_ADAPTER_BASE_URL 的本地默认一致） */
const ADAPTER_BASE = process.env.OA_E2E_ADAPTER_BASE_URL ?? 'http://localhost:8000';

test.beforeEach(async ({ request }) => {
  // 前置探测：全量栈没起时 skip 而不是红——e2e 的价值在链路，不在报连接错误。
  const health = await request.get(`${ADAPTER_BASE}/healthz`).catch(() => null);
  if (health === null || !health.ok()) {
    test.skip(true, `本地全量栈未运行（${ADAPTER_BASE}/healthz 不可达），先起 deploy 栈再跑`);
  }
});

test.describe('贯穿旅程（需真实测试账号）', () => {
  test.skip(
    !E2E_USERNAME || !E2E_PASSWORD,
    '未设置 OA_E2E_USERNAME / OA_E2E_PASSWORD，无法走真实统一登录',
  );

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
    await expect(page.getByText('企业级办公 AI 平台').first()).toBeVisible();

    // ⑥ 插件旅程：进入插件页，安装第一个可安装插件
    await page.goto('/plugins');
    await expect(page).not.toHaveURL(/\/login/);
    const installButton = page.getByRole('button', { name: '查看并安装' }).first();
    await expect(installButton).toBeVisible();
    await installButton.click();
    // 安装抽屉打开（授权配置面板）即证明安装链路入口可达；真实提交依赖底座
    // 插件运行时，抽屉内提交留给插件域专项用例（grant-drawer 单测已覆盖逻辑）。
    await expect(page.getByRole('dialog').or(page.getByText('能力授权'))).toBeVisible();
  });
});

test.describe('贯穿旅程的匿名前置段（零夹具，随时可跑）', () => {
  // 刻意放在同一文件：贯穿旅程 skip 时，本段保证该 spec 在 CI/本地仍有
  // 确定性断言在跑，文件不沦为"整段 skip 的摆设"。

  async function expectLoginGate(page: Page, path: string): Promise<void> {
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
