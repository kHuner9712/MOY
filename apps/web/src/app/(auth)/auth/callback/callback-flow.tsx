'use client';

import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ErrorState, LoadingState } from '@/components/ui/feedback';
import { setAccessToken } from '@/lib/api-client';
import { completeAuthorization } from '@/lib/oidc';
import { clearPending, loadPending, saveSession } from '@/lib/session-store';

/**
 * 统一登录回跳的实际处理流程（挂载后立即执行，不渲染任何中间数据）。
 *
 * 为什么把流程放在子组件而不是页面里：`page.tsx` 需要导出 `metadata`
 * （服务端），而这一段必须跑在浏览器里（要读地址栏、要碰存储、要用 Web Crypto）。
 *
 * 步骤与每一步的**安全约束**（顺序不可调整）：
 * 1. 先取地址栏 query，取到后立刻 `replaceState` 清掉——授权码不应留在历史与 Referer；
 * 2. 解析 query：服务端拒绝 / 缺授权码 / 缺一次性随机值 → 直接失败，不兑换；
 * 3. 读「待完成登录」：不存在或超过 10 分钟 → 失败，不兑换；
 * 4. 逐字比对一次性随机值：不相等 → 失败，**不兑换、不写任何存储**
 *    （否则伪造一个跳转就能把用户正在进行的正常登录清掉）；
 * 5. 校验通过后才兑换（S256，公开客户端，不带客户端密钥）；
 * 6. 兑换成功 → 写会话 → 注入内存 → 进入工作台；写不进存储 → 明确失败，
 *    不给「看起来登录了其实没登录」的假象。
 *
 * 已知残余风险（App Router 设计所致，本页无法消除）：
 * 授权码会出现在**首屏响应体的 RSC flight 载荷**里（形如 `"q":"?code=...&state=..."`）。
 * Referrer 泄露面已用响应头堵住（`next.config.js` 的 `/auth/callback` 规则下发
 * `no-referrer`，优先于页面 metadata）；但「参数不会到达客户端」这类说法是错的——
 * 清地址栏只能缩短它在浏览器历史与后续请求里的存活时间。真要在网关层也不留痕，
 * 需要改成「后端代理兑换」，属本轮冻结方案之外。
 */

/** 登录成功后落地页 */
const POST_LOGIN_PATH = '/chat';

/** 统一登录未配置时的业务语言提示 */
const NOT_CONFIGURED_MESSAGE = '统一登录尚未配置，请联系管理员';

/** 失败态的统一文案（业务语言：不提任何协议参数与内部原因） */
const FAILURE_MESSAGE = '本次登录未完成，请重新登录';

/** 浏览器无法保存登录状态时的业务语言提示 */
const STORAGE_UNAVAILABLE_MESSAGE = '当前环境无法保存登录状态，请更换浏览器或联系管理员';

/** 流程阶段 */
type Phase = 'working' | 'failed';

/**
 * 回跳流程组件。
 *
 * @returns 回跳页元素
 */
export function CallbackFlow(): React.JSX.Element {
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>('working');
  const [failureMessage, setFailureMessage] = useState<string>(FAILURE_MESSAGE);
  /**
   * 只跑一次的闸门，用 ref 而不是 state。
   *
   * 严格模式下 effect 会在 mount 后连续调用两次，而**同一 commit 内 state 更新
   * 尚未生效**（第二次调用读到的仍是 false），用 state 做闸门等于没闸门：
   * 授权码是一次性的，第二次兑换必然失败，界面会先闪一下「本次登录未完成」。
   * ref 是同步可写的，才能在第一次调用时就把门关上。
   */
  const startedRef = useRef(false);

  /**
   * 失败收尾：只切界面状态，**不碰任何存储**。
   *
   * 为什么连「清一下更干净」都不做：校验未通过时（尤其一次性随机值不匹配）
   * 这次跳转本身就是不可信的，此时动存储等于允许伪造链接清掉用户正在用的会话。
   * 已有会话是否还有效，由 `useSession` 按过期时间统一判定。
   */
  const fail = useCallback((message: string) => {
    setFailureMessage(message);
    setPhase('failed');
  }, []);

  useEffect(() => {
    if (startedRef.current || typeof window === 'undefined') return;
    startedRef.current = true;

    const issuer = (process.env.NEXT_PUBLIC_OIDC_ISSUER ?? '').trim();
    const clientId = (process.env.NEXT_PUBLIC_OIDC_CLIENT_ID ?? '').trim();
    if (issuer === '' || clientId === '') {
      fail(NOT_CONFIGURED_MESSAGE);
      return;
    }

    // ① 先取参数，取到后立刻清地址栏（缩短授权码在历史/Referer 里的存活窗口）
    const search = window.location.search;
    window.history.replaceState({}, '', '/auth/callback');

    const run = async (): Promise<void> => {
      // ②③④ 解析 + 校验 + 兑换：全部收敛在纯函数里，便于门禁逐条断言。
      // `onValidated` 在**兑换之前**被调用：一次性随机值一旦比对通过就作废，
      // 不留「兑换挂在网络上、本地状态还留着」的可重放窗口。
      const outcome = await completeAuthorization({
        search,
        pending: loadPending(),
        issuer,
        clientId,
        onValidated: clearPending,
      });

      // 判定过期时也要清掉：留着只会让用户每次回来都撞同一个死状态
      if (outcome.consumePending) clearPending();

      if (outcome.kind !== 'authorized' || !outcome.session) {
        fail(FAILURE_MESSAGE);
        return;
      }

      // ⑥ 落库失败必须明确失败：内存里有令牌、存储里没有，刷新后就会「掉线」
      if (!saveSession(outcome.session)) {
        fail(STORAGE_UNAVAILABLE_MESSAGE);
        return;
      }

      setAccessToken(outcome.session.access_token);
      router.replace(POST_LOGIN_PATH);
    };

    void run().catch(() => {
      // 任何未预期异常都按「本次登录未完成」处理，不向用户暴露内部细节
      fail(FAILURE_MESSAGE);
    });
  }, [fail, router]);

  const handleRetry = useCallback(() => {
    router.replace('/login');
  }, [router]);

  if (phase === 'working') {
    return (
      <Card>
        <CardHeader className="text-center">
          <CardTitle className="text-xl">正在完成登录</CardTitle>
          <CardDescription>请稍候，正在验证本次登录</CardDescription>
        </CardHeader>
        <CardContent>
          <LoadingState label="正在完成登录…" description="通常只需要几秒，请不要关闭页面" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="text-center">
        <CardTitle className="text-xl">本次登录未完成</CardTitle>
        <CardDescription>可以重新登录，或联系管理员协助处理</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <ErrorState message={failureMessage} retryLabel="重新登录" onRetry={handleRetry} />
        <Button asChild variant="outline" className="w-full">
          <a href="/login">返回登录页</a>
        </Button>
      </CardContent>
    </Card>
  );
}
