import type React from "react";
import type { Metadata, Viewport } from 'next';

import { Toaster } from '@/components/ui/toast';

import '@/styles/globals.css';

/** 应用元信息 */
export const metadata: Metadata = {
  title: {
    default: '企业级办公 AI 平台',
    template: '%s · 企业级办公 AI 平台',
  },
  description: '面向企业与团队用户的跨生态办公 AI 平台，开箱即用，无需了解任何技术细节。',
  robots: { index: false, follow: false },
};

/** 视口配置（移动端适配） */
export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: '#ffffff',
};

/**
 * 根布局。
 *
 * @param props - 布局属性
 * @returns 根布局元素
 */
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body className="min-h-screen bg-background font-sans text-foreground antialiased">
        {children}
        <Toaster />
      </body>
    </html>
  );
}
