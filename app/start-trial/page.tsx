"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";

import { BUILTIN_INDUSTRY_TEMPLATE_SEEDS } from "@/data/industry-templates";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { buildClientPublicCommercialEntryInput, generateCommercialTraceId } from "@/lib/commercial-entry";

export default function StartTrialPage(): JSX.Element {
  const router = useRouter();
  const [entryTraceId] = useState(() => generateCommercialTraceId("trial"));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    contactName: "",
    companyName: "",
    email: "",
    phone: "",
    industryHint: "",
    teamSizeHint: "",
    preferredTemplateKey: "generic",
    needImportData: true,
    useCaseHint: "",
    website: ""
  });

  const submit = async (): Promise<void> => {
    setSubmitting(true);
    setError(null);
    try {
      const entryContext = buildClientPublicCommercialEntryInput({
        fallbackLandingPage: "/start-trial",
        entryTraceId
      });
      const response = await fetch("/api/public/start-trial", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          ...form,
          ...entryContext
        })
      });
      const payload = (await response.json()) as {
        success: boolean;
        error: string | null;
      };
      if (!response.ok || !payload.success) {
        throw new Error(payload.error ?? "start_trial_failed");
      }
      router.push("/start-trial/success");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "start_trial_failed");
      setSubmitting(false);
      return;
    }
    setSubmitting(false);
  };

  return (
    <main className="min-h-screen bg-slate-50">
      <section className="mx-auto max-w-3xl px-4 py-10 md:px-8">
        <Card>
          <CardHeader>
            <CardTitle className="text-2xl">Start MOY Trial</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-2 md:grid-cols-2">
              <div className="space-y-1">
                <Label>Name</Label>
                <Input value={form.contactName} onChange={(event) => setForm((prev) => ({ ...prev, contactName: event.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>Company</Label>
                <Input value={form.companyName} onChange={(event) => setForm((prev) => ({ ...prev, companyName: event.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>Email</Label>
                <Input value={form.email} onChange={(event) => setForm((prev) => ({ ...prev, email: event.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>Phone</Label>
                <Input value={form.phone} onChange={(event) => setForm((prev) => ({ ...prev, phone: event.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>Industry</Label>
                <Input value={form.industryHint} onChange={(event) => setForm((prev) => ({ ...prev, industryHint: event.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>Team Size</Label>
                <Input value={form.teamSizeHint} onChange={(event) => setForm((prev) => ({ ...prev, teamSizeHint: event.target.value }))} />
              </div>
            </div>

            <div className="space-y-1">
              <Label>Preferred Template</Label>
              <select
                className="h-10 w-full rounded-md border px-3 text-sm"
                value={form.preferredTemplateKey}
                onChange={(event) => setForm((prev) => ({ ...prev, preferredTemplateKey: event.target.value }))}
              >
                {BUILTIN_INDUSTRY_TEMPLATE_SEEDS.map((item) => (
                  <option key={item.templateKey} value={item.templateKey}>
                    {item.displayName}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-1">
              <Label>Use case</Label>
              <Textarea
                rows={4}
                value={form.useCaseHint}
                onChange={(event) => setForm((prev) => ({ ...prev, useCaseHint: event.target.value }))}
                placeholder="What outcome do you expect in the first two weeks?"
              />
            </div>

            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={form.needImportData}
                onChange={(event) => setForm((prev) => ({ ...prev, needImportData: event.target.checked }))}
              />
              We have existing customer data and need import support.
            </label>

            <input
              type="text"
              value={form.website}
              onChange={(event) => setForm((prev) => ({ ...prev, website: event.target.value }))}
              className="hidden"
              tabIndex={-1}
              autoComplete="off"
            />

            {error ? <p className="text-sm text-rose-600">{error}</p> : null}
            <div className="flex flex-wrap gap-2">
              <Button disabled={submitting} onClick={() => void submit()}>
                {submitting ? "Submitting..." : "Submit Trial Request"}
              </Button>
              <Button asChild variant="outline">
                <Link href="/request-demo">Need guided demo first?</Link>
              </Button>
            </div>
          </CardContent>
        </Card>
      </section>
    </main>
  );
}
