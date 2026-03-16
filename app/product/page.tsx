import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const modules = [
  {
    title: "Capture to Structured Followup",
    detail: "Turn free text, meeting notes, and touchpoint inputs into structured records and next actions."
  },
  {
    title: "Today Execution Rhythm",
    detail: "Generate explainable task priorities and keep execution focused on high-value, high-risk opportunities."
  },
  {
    title: "Preparation Layer",
    detail: "Followup prep cards, quote prep, meeting prep, morning briefings, and editable draft generation."
  },
  {
    title: "Deal Command Center",
    detail: "Deal rooms, checkpoints, intervention requests, and lightweight decision records for critical opportunities."
  },
  {
    title: "Closed-Loop Learning",
    detail: "Capture outcomes, adoption signals, effectiveness patterns, and reusable playbooks."
  },
  {
    title: "Productization Ready",
    detail: "Org settings, feature flags, AI control center, usage quotas, onboarding, imports, mobile PWA."
  }
];

export default function ProductPage(): JSX.Element {
  return (
    <main className="min-h-screen bg-slate-50">
      <section className="mx-auto max-w-6xl px-4 py-10 md:px-8">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-sky-700">MOY Product</p>
        <h1 className="mt-2 text-3xl font-bold tracking-tight text-slate-900 md:text-4xl">One operating system for B2B sales execution</h1>
        <p className="mt-3 max-w-3xl text-sm text-slate-600">
          MOY is built for small and medium sales teams who need better execution visibility, faster followup, and
          manager-level intervention before opportunities are lost.
        </p>

        <div className="mt-6 flex flex-wrap gap-2">
          <Button asChild>
            <Link href="/request-demo">Request Demo</Link>
          </Button>
          <Button asChild variant="outline">
            <Link href="/start-trial">Start Trial</Link>
          </Button>
        </div>

        <section className="mt-8 grid gap-4 md:grid-cols-2">
          {modules.map((item) => (
            <Card key={item.title}>
              <CardHeader>
                <CardTitle className="text-lg">{item.title}</CardTitle>
              </CardHeader>
              <CardContent className="text-sm text-slate-700">{item.detail}</CardContent>
            </Card>
          ))}
        </section>
      </section>
    </main>
  );
}
