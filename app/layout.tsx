import type { Metadata, Viewport } from "next";

import "@/app/globals.css";
import { RootProviders } from "@/components/shared/root-providers";

export const metadata: Metadata = {
  title: "MOY 墨言 | 桐鸣科技",
  description: "面向中小企业销售团队的 Web AI 工作台",
  manifest: "/manifest.webmanifest"
};

export const viewport: Viewport = {
  themeColor: "#0ea5e9"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>): JSX.Element {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body className="font-sans antialiased">
        <RootProviders>{children}</RootProviders>
      </body>
    </html>
  );
}
