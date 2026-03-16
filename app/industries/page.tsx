import Link from "next/link";

import { BUILTIN_INDUSTRY_TEMPLATE_SEEDS } from "@/data/industry-templates";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function IndustriesPage(): JSX.Element {
  return (
    <main className="min-h-screen bg-slate-50">
      <section className="mx-auto max-w-6xl px-4 py-10 md:px-8">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-sky-700">Industry Templates</p>
        <h1 className="mt-2 text-3xl font-bold tracking-tight text-slate-900 md:text-4xl">
          Industry-first sales operating templates
        </h1>
        <p className="mt-3 max-w-3xl text-sm text-slate-600">
          Select the right template during onboarding, demo, or trial bootstrap to get aligned stages, checkpoints,
          alerts, and playbook seeds from day one.
        </p>

        <section className="mt-8 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {BUILTIN_INDUSTRY_TEMPLATE_SEEDS.map((item) => (
            <Card key={item.templateKey}>
              <CardHeader>
                <CardTitle className="text-lg">{item.displayName}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm text-slate-700">
                <p>{item.summary}</p>
                <p className="text-xs text-muted-foreground">key={item.templateKey}</p>
                <p className="text-xs text-muted-foreground">family={item.industryFamily}</p>
              </CardContent>
            </Card>
          ))}
        </section>

        <div className="mt-8 flex flex-wrap gap-2">
          <Button asChild>
            <Link href="/request-demo">Book Industry Demo</Link>
          </Button>
          <Button asChild variant="outline">
            <Link href="/start-trial">Start Industry Trial</Link>
          </Button>
        </div>
      </section>
    </main>
  );
}
