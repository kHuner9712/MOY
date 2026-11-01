/**
 * 前端补测第三十四批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：全局提示容器的装配口径 —— 顶部居中、4 秒自动消失、带关闭按钮；
 * `toast` 导出可用（业务文案由调用方给出，本模块不产生任何文案）。
 */

import { describe, expect, it } from 'vitest';

import { renderComponent } from '@/test/render-hook';

import { Toaster, toast } from './toast';

describe('全局提示容器', () => {
  it('装配口径：顶部居中、4 秒、可关闭', () => {
    // 装配参数是合同的一部分（时长与位置改了会影响所有提示的体感）
    const element = Toaster();
    expect(element.props.position).toBe('top-center');
    expect(element.props.duration).toBe(4000);
    expect(element.props.closeButton).toBe(true);
    expect(element.props.richColors).toBe(true);
  });

  it('容器可渲染，toast 导出可用', () => {
    const { container, unmount } = renderComponent(<Toaster />);
    expect(container).toBeTruthy();
    // 提示入口只导出能力，文案全部由调用方以业务语言给出
    expect(typeof toast.success).toBe('function');
    expect(typeof toast.error).toBe('function');
    unmount();
  });
});
