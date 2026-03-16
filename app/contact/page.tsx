"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export default function ContactPage(): JSX.Element {
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    contactName: "",
    companyName: "",
    email: "",
    phone: "",
    industryHint: "",
    teamSizeHint: "",
    message: "",
    website: ""
  });

  const submit = async (): Promise<void> => {
    setSubmitting(true);
    setMessage(null);
    setError(null);
    try {
      const response = await fetch("/api/public/contact", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(form)
      });
      const payload = (await response.json()) as { success: boolean; error: string | null };
      if (!response.ok || !payload.success) throw new Error(payload.error ?? "contact_form_failed");
      setMessage("Submitted successfully. Our team will contact you soon.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "contact_form_failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="min-h-screen bg-slate-50">
      <section className="mx-auto max-w-3xl px-4 py-10 md:px-8">
        <Card>
          <CardHeader>
            <CardTitle>Contact MOY Team</CardTitle>
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
            </div>
            <div className="grid gap-2 md:grid-cols-2">
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
              <Label>Message</Label>
              <Textarea rows={5} value={form.message} onChange={(event) => setForm((prev) => ({ ...prev, message: event.target.value }))} />
            </div>
            <input
              type="text"
              value={form.website}
              onChange={(event) => setForm((prev) => ({ ...prev, website: event.target.value }))}
              className="hidden"
              tabIndex={-1}
              autoComplete="off"
            />
            {message ? <p className="text-sm text-emerald-700">{message}</p> : null}
            {error ? <p className="text-sm text-rose-600">{error}</p> : null}
            <Button disabled={submitting} onClick={() => void submit()}>
              {submitting ? "Submitting..." : "Submit"}
            </Button>
          </CardContent>
        </Card>
      </section>
    </main>
  );
}
