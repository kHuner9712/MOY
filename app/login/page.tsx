"use client";

import { useAuth } from "@/components/auth/auth-provider";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { authService } from "@/services/auth-service";
import { ShieldCheck, Sparkles, Target } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

export default function LoginPage(): JSX.Element {
  const router = useRouter();
  const { user, loading, loginWithPassword } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [redirectTo, setRedirectTo] = useState("/dashboard");

  const demoAuthEnabled = authService.isDemoAuthEnabled();
  const demoAccounts = authService.getDemoAccounts();

  useEffect(() => {
    const queryRedirect = new URLSearchParams(window.location.search).get("redirectTo");
    if (queryRedirect) {
      setRedirectTo(queryRedirect);
    }
  }, []);

  useEffect(() => {
    if (!loading && user) {
      router.replace("/dashboard");
    }
  }, [user, loading, router]);

  const submitLogin = async (nextEmail: string, nextPassword: string): Promise<void> => {
    setPending(true);
    setError(null);
    const result = await loginWithPassword(nextEmail, nextPassword);
    setPending(false);
    if (!result.success) {
      setError(result.error ?? "登录失败，请检查账号和密码。");
      return;
    }
    router.replace(redirectTo);
  };

  const handleFormSubmit = async (event: React.FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (!email.trim() || !password.trim()) {
      setError("请输入邮箱和密码。");
      return;
    }
    await submitLogin(email.trim(), password);
  };

  const handleDemoLogin = async (demoEmail: string): Promise<void> => {
    await submitLogin(demoEmail, authService.getDemoDefaultPassword());
  };

  return (
    <div className="relative min-h-screen overflow-hidden bg-slate-50">
      <div className="absolute inset-0 -z-10 bg-[radial-gradient(circle_at_top_right,rgba(14,116,144,0.16),transparent_42%),radial-gradient(circle_at_bottom_left,rgba(15,23,42,0.1),transparent_45%)]" />
      <div className="mx-auto flex min-h-screen max-w-6xl flex-col justify-center px-4 py-12 lg:grid lg:grid-cols-[1.1fr_0.9fr] lg:items-center lg:gap-10">
        <section className="mb-10 lg:mb-0">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-sky-700">桐鸣科技</p>
          <h1 className="mt-4 font-display text-5xl font-semibold leading-tight text-slate-900">MOY 墨言</h1>
          <p className="mt-3 text-lg font-medium text-slate-700">面向中小企业销售团队的 Web AI 工作台</p>
          <p className="mt-6 max-w-xl text-sm leading-6 text-slate-600">
            让销售快速记录沟通内容，系统自动给出客户分析、跟进建议与漏单提醒，让管理者实时看见团队真实推进状态。
          </p>

          <div className="mt-8 grid gap-3 sm:grid-cols-3">
            <Feature icon={Target} title="推进更快" desc="客户沟通录入后自动沉淀行动建议" />
            <Feature icon={Sparkles} title="AI 辅助" desc="生成客户状态总结与风险判断" />
            <Feature icon={ShieldCheck} title="管理可视" desc="老板看板直接洞察团队推进质量" />
          </div>
        </section>

        <Card className="border-slate-200/80 bg-white/90 shadow-panel">
          <CardHeader>
            <CardTitle className="text-xl">登录 MOY 工作台</CardTitle>
            <CardDescription>使用 Supabase Auth 邮箱密码登录。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <form className="space-y-3" onSubmit={(event) => void handleFormSubmit(event)}>
              <div>
                <Label htmlFor="email">邮箱</Label>
                <Input
                  id="email"
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="name@company.com"
                  className="mt-1"
                />
              </div>
              <div>
                <Label htmlFor="password">密码</Label>
                <Input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="请输入密码"
                  className="mt-1"
                />
              </div>
              {error ? <p className="text-xs text-rose-600">{error}</p> : null}
              <Button className="w-full" type="submit" disabled={pending || loading}>
                {pending ? "登录中..." : "登录"}
              </Button>
            </form>

            {demoAuthEnabled ? (
              <>
                <Separator />
                <div className="space-y-2">
                  <p className="text-xs font-medium uppercase tracking-[0.15em] text-slate-500">开发模式 Demo 登录</p>
                  <div className="grid gap-2">
                    {demoAccounts.map((account) => (
                      <Button
                        key={account.email}
                        variant="outline"
                        className="h-auto justify-between py-3"
                        onClick={() => void handleDemoLogin(account.email)}
                        disabled={pending || loading}
                      >
                        <span className="text-left">
                          <span className="block font-semibold">{account.label}</span>
                          <span className="block text-xs text-muted-foreground">{account.email}</span>
                        </span>
                        <span className="text-xs text-slate-500">一键登录</span>
                      </Button>
                    ))}
                  </div>
                </div>
              </>
            ) : null}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function Feature({
  icon: Icon,
  title,
  desc
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  desc: string;
}): JSX.Element {
  return (
    <div className="rounded-xl border border-slate-200/80 bg-white/80 p-4 shadow-sm">
      <div className="mb-2 inline-flex h-8 w-8 items-center justify-center rounded-full bg-sky-100 text-sky-700">
        <Icon className="h-4 w-4" />
      </div>
      <p className="text-sm font-semibold text-slate-900">{title}</p>
      <p className="mt-1 text-xs leading-5 text-slate-600">{desc}</p>
    </div>
  );
}
