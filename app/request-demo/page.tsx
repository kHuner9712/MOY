"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

interface SubmitState {
  submitting: boolean;
  error: string | null;
}

export default function RequestDemoPage(): JSX.Element {
  const router = useRouter();
  const [state, setState] = useState<SubmitState>({
    submitting: false,
    error: null
  });
  const [form, setForm] = useState({
    contactName: "",
    companyName: "",
    email: "",
    phone: "",
    industryHint: "",
    teamSizeHint: "",
    useCaseHint: "",
    scenarioFocus: "",
    preferredTimeText: "",
    website: ""
  });

  const submit = async (): Promise<void> => {
    setState({
      submitting: true,
      error: null
    });
    try {
      const res = await fetch("/api/public/request-demo", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(form)
      });
      const payload = (await res.json()) as {
        success: boolean;
        error: string | null;
      };
      if (!res.ok || !payload.success) {
        throw new Error(payload.error ?? "request_demo_failed");
      }
      router.push("/request-demo/success");
    } catch (error) {
      setState({
        submitting: false,
        error: error instanceof Error ? error.message : "request_demo_failed"
      });
      return;
    }
    setState({
      submitting: false,
      error: null
    });
  };

  return (
    <main className="min-h-screen bg-slate-50">
      <section className="mx-auto max-w-3xl px-4 py-10 md:px-8">
        <Card>
          <CardHeader>
            <CardTitle className="text-2xl">Request MOY Demo</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-2 md:grid-cols-2">
              <div className="space-y-1">
                <Label>Name</Label>
                <Input value={form.contactName} onChange={(event) => setForm((prev) => ({ ...prev, contactName: event.target.value }))} placeholder="Your name" />
              </div>
              <div className="space-y-1">
                <Label>Company</Label>
                <Input value={form.companyName} onChange={(event) => setForm((prev) => ({ ...prev, companyName: event.target.value }))} placeholder="Company name" />
              </div>
              <div className="space-y-1">
                <Label>Email</Label>
                <Input value={form.email} onChange={(event) => setForm((prev) => ({ ...prev, email: event.target.value }))} placeholder="name@company.com" />
              </div>
              <div className="space-y-1">
                <Label>Phone</Label>
                <Input value={form.phone} onChange={(event) => setForm((prev) => ({ ...prev, phone: event.target.value }))} placeholder="Optional" />
              </div>
              <div className="space-y-1">
                <Label>Industry</Label>
                <Input value={form.industryHint} onChange={(event) => setForm((prev) => ({ ...prev, industryHint: event.target.value }))} placeholder="e.g. B2B software" />
              </div>
              <div className="space-y-1">
                <Label>Team Size</Label>
                <Input value={form.teamSizeHint} onChange={(event) => setForm((prev) => ({ ...prev, teamSizeHint: event.target.value }))} placeholder="e.g. 5-20 sales reps" />
              </div>
            </div>

            <div className="space-y-1">
              <Label>Current challenge</Label>
              <Textarea
                value={form.useCaseHint}
                onChange={(event) => setForm((prev) => ({ ...prev, useCaseHint: event.target.value }))}
                placeholder="What problem do you want MOY to solve first?"
                rows={4}
              />
            </div>
            <div className="space-y-1">
              <Label>Scenario you want to see</Label>
              <Input value={form.scenarioFocus} onChange={(event) => setForm((prev) => ({ ...prev, scenarioFocus: event.target.value }))} placeholder="capture / deal room / manager view / onboarding..." />
            </div>
            <div className="space-y-1">
              <Label>Preferred time</Label>
              <Input value={form.preferredTimeText} onChange={(event) => setForm((prev) => ({ ...prev, preferredTimeText: event.target.value }))} placeholder="Optional" />
            </div>

            <input
              type="text"
              value={form.website}
              onChange={(event) => setForm((prev) => ({ ...prev, website: event.target.value }))}
              className="hidden"
              tabIndex={-1}
              autoComplete="off"
            />

            {state.error ? <p className="text-sm text-rose-600">{state.error}</p> : null}

            <div className="flex flex-wrap items-center gap-2">
              <Button disabled={state.submitting} onClick={() => void submit()}>
                {state.submitting ? "Submitting..." : "Submit Demo Request"}
              </Button>
              <Button asChild variant="outline">
                <Link href="/start-trial">Prefer self-serve trial?</Link>
              </Button>
            </div>
          </CardContent>
        </Card>
      </section>
    </main>
  );
}
