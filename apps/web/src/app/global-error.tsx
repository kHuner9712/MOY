'use client';

import * as React from 'react';

/**
 * 根级兜底页（B15）。
 *
 * 触发场景：根布局或页面渲染过程中抛出未捕获异常。
 *
 * 硬约定：
 * - 只使用业务语言，**不展示错误码、堆栈、组件名、技术名词**；
 * - 不用任何 UI 库组件：这一层可能连全局样式都没加载，因此使用内联样式自洽；
 * - 提供两条出路：「重新加载」（彻底恢复）与「重试」（React 重新渲染该段）。
 *
 * @param props - Next.js 注入的错误与重置函数
 * @returns 兜底页元素
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}): React.JSX.Element {
  React.useEffect(() => {
    // 原始信息只进控制台，便于排查；页面上不展示
    console.error('[global-error]', error);
  }, [error]);

  const buttonBase: React.CSSProperties = {
    borderRadius: 8,
    border: '1px solid #d4d4d8',
    padding: '8px 16px',
    fontSize: 14,
    fontWeight: 500,
    cursor: 'pointer',
    background: '#ffffff',
    color: '#18181b',
  };

  return (
    <html lang="zh-CN">
      <body
        style={{
          margin: 0,
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#fafafa',
          color: '#18181b',
          fontFamily:
            'system-ui, -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
        }}
      >
        <main
          style={{
            maxWidth: 480,
            padding: 32,
            textAlign: 'center',
            background: '#ffffff',
            border: '1px solid #e4e4e7',
            borderRadius: 12,
          }}
        >
          <h1 style={{ fontSize: 18, fontWeight: 600, margin: '0 0 8px' }}>页面暂时无法显示</h1>
          <p style={{ fontSize: 14, lineHeight: 1.7, color: '#52525b', margin: '0 0 24px' }}>
            这是临时问题，页面数据没有丢失。可以先点「重试」，仍然不行再「重新加载」；
            如果多次尝试都失败，请联系团队管理员。
          </p>
          <div style={{ display: 'flex', gap: 12, justifyContent: 'center' }}>
            <button
              type="button"
              onClick={reset}
              style={{
                ...buttonBase,
                background: '#2563eb',
                borderColor: '#2563eb',
                color: '#ffffff',
              }}
            >
              重试
            </button>
            <button type="button" onClick={() => window.location.reload()} style={buttonBase}>
              重新加载
            </button>
          </div>
        </main>
      </body>
    </html>
  );
}
