'use client';

import type React from 'react';

import { useRouter } from 'next/navigation';
import { useCallback, useState } from 'react';

import { BrandMark } from '@/components/brand/brand-mark';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { toast } from '@/components/ui/toast';
import { getAdapterBaseUrl } from '@/lib/api-client';
import { BRAND } from '@/lib/brand';
import { toBusinessMessage } from '@/lib/business-errors';
import { resolveNextPath } from '@/lib/redirect';
import { startLogin } from '@/lib/session-client';

/**
 * 登录页（AR-013 / D-26：生产构建不得提供任何绕过登录的入口）。
 *
 * 设计要点（总方案 5.5 / 附录 A-1）：
 * - 界面不出现任何技术术语、组件名称、身份服务品牌名——统一称「统一登录」；
 * - **生产构建**只有一条路径：跳转统一登录；
 * - **开发构建**保留「进入工作台」入口便于本地联调，但必须显式标注「仅开发环境」。
 *
 * 本页在 P0-5 路线 A 里的变化（令牌不进 JavaScript）：
 * - 不再生成 PKCE、不再拼授权地址、不再存任何东西：`verifier` 与 `state` 现在
 *   只存在于服务端的 httpOnly 暂存 Cookie 里，前端连「存」的机会都没有了；
 * - 统一登录的完整配置（身份服务地址、客户端标识、回跳地址、授权范围）已全部
 *   搬到服务端 —— 前端既不知道也不需要知道，少一处配置就少一处漂移；
 * - 因此**不再有「统一登录尚未配置」的本地判定**：能不能登录由
 *   `POST /sso/start` 的响应回答（未启用时是「统一登录未启用」），前端如实转述。
 *
 * 一并删除的还有「手粘访问凭证进入」这条开发路径：令牌已经不可能被写进任何
 * 请求（身份只走 Cookie），保留一个「粘贴后看起来登录了但每个请求都匿名」的
 * 入口，比没有入口更糟。
 *
 * @returns 登录页元素
 */

/** 是否生产构建（Next 在构建期静态替换该表达式） */
const IS_PRODUCTION_BUILD = process.env.NODE_ENV === 'production';

/**
 * 读取地址栏里的 `next` 原始值（登录后要回到哪里）。
 *
 * 为什么直接读 `window.location.search` 而不是 `useSearchParams()`：
 * 后者会让整个页面被要求包一层 `Suspense`（App Router 的静态生成约束），
 * 而本页的交互全部发生在浏览器里，读地址栏即可；这里只在点击时使用，
 * 不参与首帧渲染，因此不需要额外的挂载时状态。
 *
 * 返回值**未校验**：必须交给 `resolveNextPath` 处理后才能使用。
 *
 * @returns 地址栏里的 `next`；不存在时为空串
 */
function readNextParam(): string {
  if (typeof window === 'undefined') {
    return '';
  }
  return new URLSearchParams(window.location.search).get('next') ?? '';
}

/**
 * 登录页组件。
 *
 * @returns 登录页元素
 */
export default function LoginPage(): React.JSX.Element {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);

  /**
   * 生产路径：向服务端要授权地址，然后整页跳过去。
   *
   * 顺序不可颠倒：**先取地址、再跳转**。取不到地址（`auth_mode=stub`、网关不可用、
   * 身份平台配置缺失）时必须停在原地给出提示——跳一个空地址会把用户带到
   * 一个更莫名其妙的错误页，而且再回来时本次登录往返已经作废了。
   */
  const handleUnifiedLogin = useCallback(async () => {
    setSubmitting(true);
    try {
      // `next` 交给服务端随一次性状态一起带走（服务端契约 `{next?}`）；
      // 越界路径已在本地被 `resolveNextPath` 过滤成默认落地页。
      const authorizeUrl = await startLogin(resolveNextPath(readNextParam()));
      window.location.assign(authorizeUrl);
      // 成功后浏览器即将离开本页，保持按钮禁用态，避免重复发起
    } catch (error) {
      setSubmitting(false);
      toast.error(toBusinessMessage(error));
    }
  }, []);

  /**
   * 开发路径：直接进入工作台（**仅开发构建可见**）。
   *
   * 生产构建下该按钮不会渲染，因此不存在「点一下就绕过登录」的可能。
   */
  const handleEnter = useCallback(() => {
    setSubmitting(true);
    try {
      toast.success('正在进入工作台');
      router.push(resolveNextPath(readNextParam()));
    } finally {
      setSubmitting(false);
    }
  }, [router]);

  return (
    <Card>
      <CardHeader className="text-center">
        <div className="mb-5 flex justify-center">
          <BrandMark size="lg" />
        </div>
        <CardTitle className="text-xl">欢迎回到{BRAND.name}</CardTitle>
        <CardDescription>
          {BRAND.tagline} · {BRAND.promise}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {IS_PRODUCTION_BUILD ? (
          <Button className="w-full" size="lg" disabled={submitting} onClick={handleUnifiedLogin}>
            统一登录
          </Button>
        ) : (
          <div className="space-y-6">
            <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              仅开发环境：以下入口绕过身份校验，正式环境不提供
            </div>

            <Button className="w-full" size="lg" disabled={submitting} onClick={handleEnter}>
              进入工作台
            </Button>

            <div className="relative">
              <div className="absolute inset-0 flex items-center">
                <span className="w-full border-t" />
              </div>
              <div className="relative flex justify-center text-xs uppercase">
                <span className="bg-card px-2 text-muted-foreground">联调验证</span>
              </div>
            </div>

            <Button
              variant="outline"
              className="w-full"
              disabled={submitting}
              onClick={handleUnifiedLogin}
            >
              统一登录（联调验证用）
            </Button>
          </div>
        )}

        {/* 服务状态只在开发构建显示：联调时必须一眼看到连的是哪个适配层，
            但它的取值是后端地址，属于「用户可见文案不得出现后端地址」的红线内容。
            写成门控而不是删掉，是为了不把「生产不暴露」做成「开发者看不见」。
            缺省基址是空串（同源），此时显示「同源」而不是一段空白。 */}
        {IS_PRODUCTION_BUILD ? null : (
          <p className="text-center text-xs text-muted-foreground">
            服务状态：{getAdapterBaseUrl() || '同源'}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
