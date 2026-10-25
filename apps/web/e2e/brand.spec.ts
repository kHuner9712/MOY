import { expect, test } from '@playwright/test';

/**
 * 品牌与登录页骨架冒烟。
 *
 * 判据只有三条：页面能打开、品牌是「麦子 / Moy」、没有残留旧品牌；
 * 具体登录链路（OIDC 兑换）需要真实 IdP，不在本冒烟范围内。
 */
test('登录页展示麦子品牌，且不残留旧品牌', async ({ page }) => {
  await page.goto('/login');

  await expect(page).toHaveTitle(/麦子/);
  await expect(page.getByRole('heading', { name: '欢迎回到麦子' })).toBeVisible();
  await expect(page.getByText('Moy', { exact: true })).toBeVisible();
  await expect(page.getByText('办公 AI 平台')).toHaveCount(0);
});

test('登录页在开发构建下保留联调入口，但标注仅开发环境', async ({ page }) => {
  await page.goto('/login');

  await expect(page.getByText('仅开发环境', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: '进入工作台' })).toBeVisible();
});
