"use client";

import { useEffect, useMemo, useState } from "react";
import { Save } from "lucide-react";

import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { settingsClientService } from "@/services/settings-client-service";
import type { OrgSettings } from "@/types/productization";

interface FormState {
  orgDisplayName: string;
  brandName: string;
  industryHint: string;
  timezone: string;
  locale: string;
  defaultCustomerStages: string;
  defaultOpportunityStages: string;
  noFollowupTimeoutDays: string;
  quotedStalledDays: string;
  highProbabilityStalledDays: string;
  defaultFollowupSlaDays: string;
}

function toFormState(settings: OrgSettings): FormState {
  return {
    orgDisplayName: settings.orgDisplayName,
    brandName: settings.brandName,
    industryHint: settings.industryHint ?? "",
    timezone: settings.timezone,
    locale: settings.locale,
    defaultCustomerStages: settings.defaultCustomerStages.join(", "),
    defaultOpportunityStages: settings.defaultOpportunityStages.join(", "),
    noFollowupTimeoutDays: String(settings.defaultAlertRules.no_followup_timeout ?? 7),
    quotedStalledDays: String(settings.defaultAlertRules.quoted_but_stalled ?? 10),
    highProbabilityStalledDays: String(settings.defaultAlertRules.high_probability_stalled ?? 5),
    defaultFollowupSlaDays: String(settings.defaultFollowupSlaDays)
  };
}

function parseList(input: string): string[] {
  return input
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

export default function OrgSettingsPage(): JSX.Element {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [settings, setSettings] = useState<OrgSettings | null>(null);
  const [form, setForm] = useState<FormState | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        setLoading(true);
        setError(null);
        const payload = await settingsClientService.getOrgSettings();
        setSettings(payload.settings);
        setForm(toFormState(payload.settings));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load org settings");
      } finally {
        setLoading(false);
      }
    };

    void load();
  }, []);

  const onboardingProgress = useMemo(() => {
    if (!settings) return 0;
    const values = Object.values(settings.onboardingStepState);
    if (values.length === 0) return 0;
    return Math.round((values.filter(Boolean).length / values.length) * 100);
  }, [settings]);

  async function handleSave(): Promise<void> {
    if (!form) return;

    setSaving(true);
    setError(null);
    try {
      const payload = await settingsClientService.updateOrgSettings({
        orgDisplayName: form.orgDisplayName,
        brandName: form.brandName,
        industryHint: form.industryHint || null,
        timezone: form.timezone,
        locale: form.locale,
        defaultCustomerStages: parseList(form.defaultCustomerStages),
        defaultOpportunityStages: parseList(form.defaultOpportunityStages),
        defaultAlertRules: {
          no_followup_timeout: Number(form.noFollowupTimeoutDays || "7"),
          quoted_but_stalled: Number(form.quotedStalledDays || "10"),
          high_probability_stalled: Number(form.highProbabilityStalledDays || "5")
        },
        defaultFollowupSlaDays: Number(form.defaultFollowupSlaDays || "3")
      });

      setSettings(payload.settings);
      setForm(toFormState(payload.settings));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save org settings");
    } finally {
      setSaving(false);
    }
  }

  if (loading || !form || !settings) {
    return <div className="text-sm text-muted-foreground">Loading organization settings...</div>;
  }

  return (
    <div>
      <PageHeader
        title="Organization Settings"
        description="Manage organization profile, locale, default stage pipeline and alert policy."
        action={
          <Button onClick={() => void handleSave()} disabled={saving}>
            <Save className="mr-2 h-4 w-4" />
            {saving ? "Saving..." : "Save Settings"}
          </Button>
        }
      />

      {error ? <p className="mb-4 text-sm text-red-600">{error}</p> : null}

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Organization Profile</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label>Display Name</Label>
              <Input value={form.orgDisplayName} onChange={(event) => setForm({ ...form, orgDisplayName: event.target.value })} />
            </div>
            <div className="space-y-2">
              <Label>Brand Name</Label>
              <Input value={form.brandName} onChange={(event) => setForm({ ...form, brandName: event.target.value })} />
            </div>
            <div className="space-y-2">
              <Label>Industry Hint</Label>
              <Input value={form.industryHint} onChange={(event) => setForm({ ...form, industryHint: event.target.value })} placeholder="Manufacturing / SaaS / Retail" />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-2">
                <Label>Timezone</Label>
                <Input value={form.timezone} onChange={(event) => setForm({ ...form, timezone: event.target.value })} />
              </div>
              <div className="space-y-2">
                <Label>Locale</Label>
                <Input value={form.locale} onChange={(event) => setForm({ ...form, locale: event.target.value })} />
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Default Pipeline</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label>Customer Stages (comma separated)</Label>
              <Textarea rows={3} value={form.defaultCustomerStages} onChange={(event) => setForm({ ...form, defaultCustomerStages: event.target.value })} />
            </div>
            <div className="space-y-2">
              <Label>Opportunity Stages (comma separated)</Label>
              <Textarea rows={3} value={form.defaultOpportunityStages} onChange={(event) => setForm({ ...form, defaultOpportunityStages: event.target.value })} />
            </div>
            <div className="space-y-2">
              <Label>Default Followup SLA Days</Label>
              <Input type="number" min={1} max={30} value={form.defaultFollowupSlaDays} onChange={(event) => setForm({ ...form, defaultFollowupSlaDays: event.target.value })} />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Default Alert Rules</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-3">
            <div className="space-y-2">
              <Label>No Followup Timeout (days)</Label>
              <Input type="number" value={form.noFollowupTimeoutDays} onChange={(event) => setForm({ ...form, noFollowupTimeoutDays: event.target.value })} />
            </div>
            <div className="space-y-2">
              <Label>Quoted but Stalled (days)</Label>
              <Input type="number" value={form.quotedStalledDays} onChange={(event) => setForm({ ...form, quotedStalledDays: event.target.value })} />
            </div>
            <div className="space-y-2">
              <Label>High Probability Stalled (days)</Label>
              <Input type="number" value={form.highProbabilityStalledDays} onChange={(event) => setForm({ ...form, highProbabilityStalledDays: event.target.value })} />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Onboarding Status</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm text-muted-foreground">
            <p>Completed: {settings.onboardingCompleted ? "Yes" : "No"}</p>
            <p>Progress: {onboardingProgress}%</p>
            <p>For full checklist, go to <code>/settings/onboarding</code>.</p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
