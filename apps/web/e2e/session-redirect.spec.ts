import { expect, test } from '@playwright/test';

/**
 * 未登录直达深链 → 回登录页并记住去向（W-02 矩阵里唯一一条 e2e「必做」用例）。
 *
 * 为什么这一条值钱：它是**零夹具、确定性最高**的一条，锁的是一整条链路——
 * `(main)/layout.tsx` 挂 `SessionGate` → 无本地会话判 `anonymous`
 * → `buildLoginHref(pathname)`（`src/lib/redirect.ts`）→ `/login?next=%2Fchat`。
 *
 * 三个环节任意一个退化都会让用户「刷新一下就从深链掉到首页」或「登录后回不到原页面」：
 * - 会话门漏挂 / 提前挂载子页面 → 深链不跳登录（用户看到空列表，以为没数据）；
 * - `next` 不编码或用了别的写法（`?next=/chat`） → 登录后回跳失效；
 * - 登录页把 `next` 当装饰不用 → 回跳失效（该侧由 `redirect.test.ts` 守）。
 *
 * 刻意**不**断言重定向前的骨架/文案：`anonymous` 态刻意不展示「未登录」字样
 * （见 `session-gate.tsx` 的状态映射表），断言它等于把实现细节写进测试。
 */
test('未登录直达 /chat 会被带回登录页并记住去向', async ({ page }) => {
  await page.goto('/chat');

  await expect(page).toHaveURL(/\/login\?next=%2Fchat$/);
});
