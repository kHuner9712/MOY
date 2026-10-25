import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright 配置（E2E 冒烟）。
 *
 * 只覆盖“页面能否打开、品牌是否正确、登录页是否诚实降级”这类骨架；
 * 业务链路仍由 vitest 与后端 pytest 承担。浏览器用本机 Chrome（channel），
 * 不下载 Playwright 自带 Chromium，避免离线/内网环境装不上。
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: 0,
  reporter: [['list']],
  use: {
    ...devices['Desktop Chrome'],
    baseURL: 'http://localhost:3100',
    channel: 'chrome',
    headless: true,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'npx next dev -p 3100',
    url: 'http://localhost:3100/login',
    reuseExistingServer: true,
    timeout: 120_000,
    stdout: 'ignore',
    stderr: 'pipe',
  },
});
