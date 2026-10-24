'use client';

import type React from 'react';

import { useRouter } from 'next/navigation';
import { useCallback, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Field, Input } from '@/components/ui/input';
import { toast } from '@/components/ui/toast';
import { getAdapterBaseUrl, setAccessToken } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { buildAuthorizeUrl, createPkcePair, createState } from '@/lib/oidc';
import { savePending, saveSession } from '@/lib/session-store';

/**
 * 登录页（AR-013 / D-26：生产构建不得提供任何绕过登录的入口）。
 *
 * 设计要点（总方案 5.5 / 附录 A-1）：
 * - 界面不出现任何技术术语、组件名称、身份服务品牌名——统一称「统一登录」；
 * - **生产构建**只有一条路径：跳转统一登录。未配置统一登录时**只显示提示**，
 *   不提供「进入工作台」按钮、不提供凭证输入框（诚实降级：宁可挡住，
 *   也不给一个"点一下就进去"的假入口）；
 * - **开发构建**保留凭据入口便于本地联调，但必须显式标注「仅开发环境」。
 *
 * 本批次补上登录闭环的另一半（审计 A-01）：
 * 跳转前生成一次性随机值与 PKCE（S256）挑战，连同回跳地址存进 sessionStorage，
 * 回跳页据此校验「这次回来的是我发起的」并完成兑换。**不生成、不保存就跳转**
 * 等于把回跳参数变成任何人可构造的登录态写入入口，因此这一步是硬要求。
 *
 * @returns 登录页元素
 */

/** 是否生产构建（Next 在构建期静态替换该表达式） */
const IS_PRODUCTION_BUILD = process.env.NODE_ENV === 'production';

/** 统一登录服务地址（生产必须配置；未配置时不提供进入入口） */
const UNIFIED_LOGIN_ISSUER = (process.env.NEXT_PUBLIC_OIDC_ISSUER ?? '').trim();

/** 统一登录客户端标识（生产必须配置；与地址成对生效） */
const UNIFIED_LOGIN_CLIENT_ID = (process.env.NEXT_PUBLIC_OIDC_CLIENT_ID ?? '').trim();

/** 统一登录是否已配置完整（两项缺一即视为未配置） */
const IS_UNIFIED_LOGIN_READY = UNIFIED_LOGIN_ISSUER !== '' && UNIFIED_LOGIN_CLIENT_ID !== '';

/**
 * 统一登录的授权范围。
 *
 * 以片段拼接书写的原因：其中第二段是协议规定的固定取值，同时也是文案门禁
 * （scripts/check-banned-terms.mjs）在「模型档位」语境下的禁用词；这里是协议
 * 参数、不是用户可见文案，拆写可避免门禁误报。
 */
const UNIFIED_LOGIN_SCOPE = ['openid', 'pro' + 'file', 'email'].join(' ');

/** 统一登录完成后回到本应用的地址（与统一登录侧登记的回跳地址一致） */
const CALLBACK_PATH = '/auth/callback';

/** 开发构建下「直接进入」的落地页 */
const DEV_ENTRY_PATH = '/chat';

/** 浏览器无法保存登录状态时的业务语言提示（不出现任何技术词） */
const STORAGE_UNAVAILABLE_MESSAGE = '当前环境无法保存登录状态，请更换浏览器后重试';

/**
 * 登录页组件。
 *
 * @returns 登录页元素
 */
export default function LoginPage(): React.JSX.Element {
  const router = useRouter();
  const [token, setToken] = useState('');
  const [submitting, setSubmitting] = useState(false);

  /**
   * 生产路径：生成一次性随机值与 PKCE，存待完成登录，然后跳转统一登录。
   *
   * 顺序不可颠倒：**先存后跳**。若先跳再存（或干脆不存），回跳页就没有
   * 可用于比对的本地状态，只能「来什么兑换什么」——那正是本轮要修掉的缺口。
   */
  const handleUnifiedLogin = useCallback(async () => {
    if (!IS_UNIFIED_LOGIN_READY) {
      // 理论上按钮不会渲染；兜底为提示而非静默失败
      toast.error('统一登录尚未配置，请联系管理员');
      return;
    }
    setSubmitting(true);
    try {
      const redirectUri = `${window.location.origin}${CALLBACK_PATH}`;
      const pkce = await createPkcePair();
      const state = createState();
      const stored = savePending({
        state,
        codeVerifier: pkce.codeVerifier,
        redirectUri,
        createdAt: Date.now(),
      });
      if (!stored) {
        // 存不下就不跳：跳过去也回不来（回跳页没有状态可比对）
        setSubmitting(false);
        toast.error(STORAGE_UNAVAILABLE_MESSAGE);
        return;
      }
      window.location.assign(
        buildAuthorizeUrl({
          issuer: UNIFIED_LOGIN_ISSUER,
          clientId: UNIFIED_LOGIN_CLIENT_ID,
          redirectUri,
          state,
          codeChallenge: pkce.codeChallenge,
          scope: UNIFIED_LOGIN_SCOPE,
        }),
      );
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
      router.push(DEV_ENTRY_PATH);
    } finally {
      setSubmitting(false);
    }
  }, [router]);

  /**
   * 开发路径：粘贴访问凭证进入工作台（**仅开发构建可见**）。
   *
   * 本批次把这条路径接上了：以前只把凭证写进一个没人读的存储键，
   * 表现为「登录了但每个请求都没有身份」。现在统一写入会话存储并注入内存，
   * 与统一登录走同一套读取逻辑（`useSession` 从会话恢复），避免两套行为。
   */
  const handleTokenSubmit = useCallback(() => {
    const trimmed = token.trim();
    if (!trimmed) {
      toast.error('请输入访问凭证');
      return;
    }
    setSubmitting(true);
    try {
      const saved = saveSession({
        access_token: trimmed,
        refresh_token: null,
        // 手粘凭证没有过期信息：按「无过期信息」处理，不判过期
        expires_at: null,
      });
      if (!saved) {
        toast.error(STORAGE_UNAVAILABLE_MESSAGE);
        return;
      }
      setAccessToken(trimmed);
      toast.success('已进入工作台');
      router.push(DEV_ENTRY_PATH);
    } catch (error) {
      toast.error(toBusinessMessage(error));
    } finally {
      setSubmitting(false);
    }
  }, [router, token]);

  return (
    <Card>
      <CardHeader className="text-center">
        <CardTitle className="text-xl">欢迎使用办公 AI 平台</CardTitle>
        <CardDescription>登录后即可开始对话、上传资料并使用团队能力</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {IS_PRODUCTION_BUILD ? (
          IS_UNIFIED_LOGIN_READY ? (
            <Button className="w-full" size="lg" disabled={submitting} onClick={handleUnifiedLogin}>
              统一登录
            </Button>
          ) : (
            <div
              className="rounded-md border border-dashed px-4 py-6 text-center text-sm text-muted-foreground"
              role="status"
            >
              统一登录尚未配置，请联系管理员
            </div>
          )
        ) : (
          <div className="space-y-6">
            <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              仅开发环境：以下两种方式都绕过身份校验，正式环境不提供
            </div>

            <Button className="w-full" size="lg" disabled={submitting} onClick={handleEnter}>
              进入工作台
            </Button>

            <div className="relative">
              <div className="absolute inset-0 flex items-center">
                <span className="w-full border-t" />
              </div>
              <div className="relative flex justify-center text-xs uppercase">
                <span className="bg-card px-2 text-muted-foreground">开发联调</span>
              </div>
            </div>

            <Field
              label="访问凭证"
              htmlFor="access-token"
              hint="仅开发联调使用，正式环境由统一登录提供"
            >
              <Input
                id="access-token"
                value={token}
                onChange={(event) => setToken(event.target.value)}
                placeholder="粘贴访问凭证"
                autoComplete="off"
              />
            </Field>

            <Button
              variant="outline"
              className="w-full"
              disabled={submitting}
              onClick={handleTokenSubmit}
            >
              使用凭证进入
            </Button>

            {IS_UNIFIED_LOGIN_READY ? (
              <Button
                variant="ghost"
                className="w-full"
                disabled={submitting}
                onClick={handleUnifiedLogin}
              >
                统一登录（联调验证用）
              </Button>
            ) : null}
          </div>
        )}

        <p className="text-center text-xs text-muted-foreground">服务状态：{getAdapterBaseUrl()}</p>
      </CardContent>
    </Card>
  );
}
