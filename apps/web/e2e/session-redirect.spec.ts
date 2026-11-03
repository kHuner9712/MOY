import { expect, test, type Page } from '@playwright/test';

/**
 * 未登录直达深链 → 回登录页并记住去向（W-02 矩阵里唯一一条 e2e「必做」用例）。
 *
 * 为什么这一条值钱：它锁的是一整条链路——
 * `(main)/layout.tsx` 挂 `SessionGate` → `fetchSession()`（`src/lib/session-client.ts`）
 * 拿到「从未登录」四元组 → `decideSession()` 判 `login`（`src/lib/auth-refresh.ts`）
 * → `buildLoginHref(pathname)`（`src/lib/redirect.ts`）→ `/login?next=%2Fchat`。
 *
 * 三个环节任意一个退化都会让用户「刷新一下就从深链掉到首页」或「登录后回不到原页面」：
 * - 会话门漏挂 / 提前挂载子页面 → 深链不跳登录（用户看到空列表，以为没数据）；
 * - `next` 不编码或用了别的写法（`?next=/chat`） → 登录后回跳失效；
 * - 登录页把 `next` 当装饰不用 → 回跳失效（该侧由 `redirect.test.ts` 守）。
 *
 * 刻意**不**断言重定向前的骨架/文案：`anonymous` 态刻意不展示「未登录」字样
 * （见 `session-gate.tsx` 的状态映射表），断言它等于把实现细节写进测试。
 *
 * 为什么探针用路由替身作答（2026-11-02 令牌链路改造后的夹具订正；断言与改造前
 * 逐字相同，一条没松）：
 * - 「未登录」的判据从「本地无会话」改成了**问 `/sso/session`**
 *   （`docs/design-2026-11-02-security-deploy.md` §2.4.7：得 `authenticated:false`
 *   才走 `anonymous` 分支跳登录）。判据搬到了服务端，旧的「零夹具」前提已不成立；
 * - 但测试拓扑里探针**永远作答不了**：dev 服务器没有 `/sso/*` 路由（生产由边缘
 *   nginx `location ^~ /sso/` 透传，见设计 §2.4.2 的同款坑），而 `auth_mode=stub`
 *   下五个端点恒 503「统一登录未启用」（`sso_routes.py` 硬约束 2：不许假装登录
 *   成功、也不许静默降级）。探针失败**不等于**未登录——`session-gate.tsx` 按设计
 *   停在错误态「登录服务暂时不可用」而不跳登录（一次网关抖动不得变成全员重新
 *   登录），实测即卡在 /chat 不跳转；
 * - 因此在 HTTP 边界替探针作答「从未登录」四元组（与 `SsoSessionData` 逐字段
 *   对齐）。被测对象仍是浏览器里的真实链路（会话门 → 三态判据 → buildLoginHref
 *   → router.replace），与改造前断言的行为完全同一条。「探针该答什么」由适配层
 *   `test_sso_routes.py` 守，不在本条重复。
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

test('未登录直达 /chat 会被带回登录页并记住去向', async ({ page }) => {
  await answerNeverLoggedIn(page);
  await page.goto('/chat');

  await expect(page).toHaveURL(/\/login\?next=%2Fchat$/);
});
