import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const highlights = [
  "Capture multi-source communication into structured followups",
  "Today workspace with AI-prioritized execution rhythm",
  "Deal Room for manager-supported collaboration and decision flow",
  "Preparation layer: prep cards, morning briefs, and editable drafts",
  "Industry templates, import center, mobile PWA, and trial onboarding"
];

export default function MarketingHomePage(): JSX.Element {
  return (
    <main className="min-h-screen bg-gradient-to-b from-slate-50 to-white">
      <section className="mx-auto max-w-6xl px-4 py-10 md:px-8 md:py-14">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-sky-700">桐鸣科技</p>
            <h1 className="mt-2 text-3xl font-bold tracking-tight text-slate-900 md:text-4xl">MOY / 墨言</h1>
            <p className="mt-2 text-sm text-slate-600">Mate Of You · Web AI workspace for SMB sales teams</p>
          </div>
          <div className="flex items-center gap-2">
            <Button asChild variant="outline">
              <Link href="/login">Login</Link>
            </Button>
            <Button asChild>
              <Link href="/request-demo">Request Demo</Link>
            </Button>
          </div>
        </div>

        <div className="mt-8 grid gap-6 md:grid-cols-[1.2fr_0.8fr]">
          <Card className="border-sky-100 bg-sky-50/60">
            <CardHeader>
              <CardTitle className="text-2xl text-slate-900 md:text-3xl">
                Sell faster with one actionable AI command workspace
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 text-sm text-slate-700">
              <p>
                MOY helps sales teams move from communication input to execution, risk handling, manager intervention,
                and conversion review in one continuous operating flow.
              </p>
              <div className="flex flex-wrap gap-2">
                <Button asChild>
                  <Link href="/request-demo">Apply for Demo</Link>
                </Button>
                <Button asChild variant="outline">
                  <Link href="/start-trial">Start Trial</Link>
                </Button>
                <Button asChild variant="outline">
                  <Link href="/industries">Industry Templates</Link>
                </Button>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Core Value</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-slate-700">
              {highlights.map((item) => (
                <p key={item}>- {item}</p>
              ))}
            </CardContent>
          </Card>
        </div>

        <section className="mt-8 grid gap-4 md:grid-cols-3">
          <Card>
            <CardHeader>
              <CardTitle>For Sales</CardTitle>
            </CardHeader>
            <CardContent className="text-sm text-slate-700">
              Quick capture, prep cards, touchpoint drafts, and today execution in mobile and desktop.
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>For Managers</CardTitle>
            </CardHeader>
            <CardContent className="text-sm text-slate-700">
              See operating quality, rhythm, outcomes, and key intervention points before deals stall.
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>For Deployment</CardTitle>
            </CardHeader>
            <CardContent className="text-sm text-slate-700">
              Trial bootstrap, onboarding checklist, imports, industry templates, and controlled AI features.
            </CardContent>
          </Card>
        </section>
      </section>
    </main>
  );
}
