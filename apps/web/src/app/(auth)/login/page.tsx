'use client';

import { useRouter } from 'next/navigation';
import { useCallback, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Field, Input } from '@/components/ui/input';
import { toast } from '@/components/ui/toast';
import { getAdapterBaseUrl } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';

/**
 * 登录页。
 *
 * 设计要点（总方案 5.5 / 附录 A-1）：
 * - 界面不出现任何技术术语、模型名称、组件名称；
 * - 生产环境的账号密码由身份服务托管页承担，
 *   本页仅提供「进入工作台」入口与开发态令牌输入（便于联调）。
 *
 * @returns 登录页元素
 */
export default function LoginPage(): JSX.Element {
  const router = useRouter();
  const [token, setToken] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleEnter = useCallback(() => {
    setSubmitting(true);
    try {
      // 生产环境此处应跳转身份服务托管登录页；
      // T01 阶段先提供占位流程，T03 接入 OIDC 授权码流程。
      toast.success('正在进入工作台');
      router.push('/chat');
    } finally {
      setSubmitting(false);
    }
  }, [router]);

  const handleTokenSubmit = useCallback(() => {
    const trimmed = token.trim();
    if (!trimmed) {
      toast.error('请输入访问凭证');
      return;
    }
    setSubmitting(true);
    try {
      // 仅开发态：直接注入令牌后进入工作台
      window.localStorage.setItem('officeai_access_token', trimmed);
      toast.success('已进入工作台');
      router.push('/chat');
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
        <Button className="w-full" size="lg" disabled={submitting} onClick={handleEnter}>
          进入工作台
        </Button>

        <div className="relative">
          <div className="absolute inset-0 flex items-center">
            <span className="w-full border-t" />
          </div>
          <div className="relative flex justify-center text-xs uppercase">
            <span className="bg-card px-2 text-muted-foreground">开发环境</span>
          </div>
        </div>

        <Field label="访问凭证" htmlFor="access-token" hint="仅开发联调使用，正式环境由统一登录提供">
          <Input
            id="access-token"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="粘贴访问凭证"
            autoComplete="off"
          />
        </Field>

        <Button variant="outline" className="w-full" disabled={submitting} onClick={handleTokenSubmit}>
          使用凭证进入
        </Button>

        <p className="text-center text-xs text-muted-foreground">
          服务状态：{getAdapterBaseUrl()}
        </p>
      </CardContent>
    </Card>
  );
}
