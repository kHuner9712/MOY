'use client';

import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ErrorState, LoadingState } from '@/components/ui/feedback';
import { parseCallbackQuery } from '@/lib/oidc';
import { resolveNextPath } from '@/lib/redirect';
import { completeLogin } from '@/lib/session-client';

/**
 * 统一登录回跳的实际处理流程（挂载后立即执行，不渲染任何中间数据）。
 *
 * 为什么把流程放在子组件而不是页面里：`page.tsx` 需要导出 `metadata`
 * （服务端），而这一段必须跑在浏览器里（要读地址栏）。
 *
 * 步骤与每一步的**安全约束**（顺序不可调整）：
 * 1. 先取地址栏 query，取到后立刻 `replaceState` 清掉——授权码不应留在历史与 Referer；
 * 2. 解析 query：服务端拒绝 / 缺授权码 / 缺一次性随机值 → 直接失败，**不发起兑换**；
 * 3. 交给服务端完成兑换（`POST /sso/complete`）：`state` 与 PKCE verifier 都在
 *    服务端的 httpOnly 暂存 Cookie 里，前端只递 `code` 与 `state`；
 * 4. 成功即进入工作台——令牌由服务端以 `Set-Cookie` 下发，前端**没有任何落库动作**
 *    （这正是本轮改造的目标：浏览器里不存在 JS 可读的令牌）。
 *
 * 与改造前的两点差异（都是**能力变强**，不是能力缺失）：
 * - 「兑换失败不写会话」这条顺序纪律现在由结构保证：本页根本不具备写会话的能力；
 * - 校验不再依赖本地存储的「待完成登录」，因此也不存在「本地状态不可信就清掉用户
 *   正常登录」的风险面——伪造一次回跳最多换来一次失败提示。
 *
 * 已知限制（**契约所致，不是缺陷**）：深链 `next` 目前无法在回跳后恢复。
 * 授权地址由服务端生成、回跳地址固定为 `/auth/callback`，`next` 只随服务端的一次性
 * 状态 Cookie 走、而 `/sso/complete` 的响应体按契约只含 `{ok, expires_in}`。
 * 因此回跳后统一回到默认落地页。要恢复深链需要服务端在 `complete` 响应里回传
 * `next`（属于后端契约变更，本轮不做）。
 *
 * 已知残余风险（App Router 设计所致，本页无法消除）：
 * 授权码会出现在**首屏响应体的 RSC flight 载荷**里（形如 `"q":"?code=...&state=..."`）。
 * Referrer 泄露面已用响应头堵住（`next.config.js` 的 `/auth/callback` 规则下发
 * `no-referrer`，优先于页面 metadata）；但「参数不会到达客户端」这类说法是错的——
 * 清地址栏只能缩短它在浏览器历史与后续请求里的存活时间。
 */

/** 失败态的统一文案（业务语言：不提任何协议参数与内部原因） */
const FAILURE_MESSAGE = '本次登录未完成，请重新登录';

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
   * 为什么连「清一下更干净」都不做：校验未通过时（尤其回跳参数异常），这次跳转
   * 本身就是不可信的，此时动任何本地状态都等于允许伪造链接影响用户正在用的会话。
   * 会话是否还有效，由会话门问服务端统一判定。
   */
  const fail = useCallback((message: string) => {
    setFailureMessage(message);
    setPhase('failed');
  }, []);

  useEffect(() => {
    if (startedRef.current || typeof window === 'undefined') return;
    startedRef.current = true;

    // ① 先取参数，取到后立刻清地址栏（缩短授权码在历史/Referer 里的存活窗口）
    const search = window.location.search;
    window.history.replaceState({}, '', '/auth/callback');

    const run = async (): Promise<void> => {
      // ② 解析：服务端拒绝 / 缺授权码 / 缺状态串 → 直接失败，一次兑换请求都不发
      const parsed = parseCallbackQuery(search);
      if (parsed.kind !== 'ok') {
        fail(FAILURE_MESSAGE);
        return;
      }

      // ③ 交给服务端兑换：state 与 verifier 只在 httpOnly 暂存 Cookie 里
      await completeLogin(parsed.code, parsed.state);

      // ④ 浏览器此时已持有会话 Cookie，直接进工作台
      router.replace(resolveNextPath(null));
    };

    void run().catch((error: unknown) => {
      // 任何未预期异常都按「本次登录未完成」处理，不向用户暴露内部细节；
      // 服务端拒绝时的文案（如「登录请求已失效，请重新发起登录」）是可操作的，
      // 因此优先用它，其余一律收敛到统一文案。
      const message =
        error instanceof Error && error.message.trim() !== '' ? error.message : FAILURE_MESSAGE;
      fail(message);
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
