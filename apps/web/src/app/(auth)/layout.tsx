import type { Metadata } from 'next';

/** 登录页元信息 */
export const metadata: Metadata = {
  title: '登录',
};

/**
 * 登录布局：独立于主界面，隐藏侧边导航。
 *
 * @param props - 布局属性
 * @returns 布局元素
 */
export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}): JSX.Element {
  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/30 px-4">
      <div className="w-full max-w-md">{children}</div>
    </div>
  );
}
