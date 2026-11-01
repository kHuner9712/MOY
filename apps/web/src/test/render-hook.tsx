/**
 * React Hook 渲染测试基建（补测第十二批引入，审计 2026-10-31 H1 续）。
 *
 * 为什么用 `react-dom/client` + `act` 而不是第三方测试库：
 * 1. Hook 的状态机（上传轮询、流式拼接、重试计数）只能在真实 Hook 运行时里验证，
 *    纯函数口径已覆盖不到这些分支；
 * 2. React 19 官方测试方式就是 `createRoot` + `act`，不引入额外依赖；
 * 3. 仍然只渲染**探针组件**（返回 null 的最小组件），不渲染任何业务 UI，
 *    保持测试轻量稳定——业务组件本身仍由 Playwright E2E 兜底。
 */

import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';

// React 19 的 `act` 要求测试环境显式声明（未设置时会警告并失去批量更新语义）
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

export { act };

/** `renderHook` 返回值 */
export interface RenderHookResult<T> {
  /** Hook 最新返回值（每次渲染后更新） */
  result: { current: T };
  /** 卸载探针组件（触发 effect 清理） */
  unmount: () => void;
}

/**
 * 挂载一个探针组件并在其中运行 Hook。
 *
 * @param useHook - 被测 Hook 调用（`() => useXxx(...)`）
 * @param options - 可选 wrapper（如 QueryClientProvider）
 * @returns Hook 结果引用与卸载函数
 */
export function renderHook<T>(
  useHook: () => T,
  options: { wrapper?: (props: { children: ReactNode }) => ReactNode } = {},
): RenderHookResult<T> {
  const result: { current: T } = { current: undefined as unknown as T };

  function Probe(): ReactNode {
    result.current = useHook();
    return null;
  }

  const container = document.createElement('div');
  document.body.appendChild(container);
  const root: Root = createRoot(container);
  const { wrapper: Wrapper } = options;

  act(() => {
    root.render(
      Wrapper ? (
        <Wrapper>
          <Probe />
        </Wrapper>
      ) : (
        <Probe />
      ),
    );
  });

  return {
    result,
    unmount: () => {
      act(() => {
        root.unmount();
      });
      container.remove();
    },
  };
}
