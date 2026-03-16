"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Save } from "lucide-react";

import { PageHeader } from "@/components/shared/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { settingsClientService } from "@/services/settings-client-service";
import type { OrgAiSettings, OrgFeatureFlag, OrgFeatureKey } from "@/types/productization";

interface AiState {
  role: string;
  canManage: boolean;
  settings: OrgAiSettings;
  providerConfigured: boolean;
  providerReason: string | null;
  featureFlags: OrgFeatureFlag[];
}

export default function AiSettingsPage(): JSX.Element {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [state, setState] = useState<AiState | null>(null);

  async function load(): Promise<void> {
    try {
      setLoading(true);
      setError(null);
      const payload = await settingsClientService.getAiSettings();
      setState({
        role: payload.role,
        canManage: payload.canManage,
        settings: payload.aiStatus.settings,
        providerConfigured: payload.aiStatus.providerConfigured,
        providerReason: payload.aiStatus.providerReason,
        featureFlags: payload.featureFlags
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load AI settings");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const featureMap = useMemo(() => {
    const map: Record<string, boolean> = {};
    for (const item of state?.featureFlags ?? []) {
      map[item.featureKey] = item.isEnabled;
    }
    return map;
  }, [state]);

  async function save(patch: Partial<{
    modelDefault: string;
    modelReasoning: string;
    fallbackMode: OrgAiSettings["fallbackMode"];
    autoAnalysisEnabled: boolean;
    autoPlanEnabled: boolean;
    autoBriefEnabled: boolean;
    autoTouchpointReviewEnabled: boolean;
    humanReviewRequiredForSensitiveActions: boolean;
    maxDailyAiRuns: number | null;
    maxMonthlyAiRuns: number | null;
    featureFlags: Partial<Record<OrgFeatureKey, boolean>>;
  }>): Promise<void> {
    if (!state?.canManage) return;

    setSaving(true);
    setError(null);
    try {
      const response = await settingsClientService.updateAiSettings({
        ...patch,
        featureFlags: patch.featureFlags as Record<OrgFeatureKey, boolean> | undefined
      });

      const nextFeatureFlags = response.featureFlags ?? state.featureFlags;
      setState({
        ...state,
        settings: response.status.settings,
        providerConfigured: response.status.providerConfigured,
        providerReason: response.status.providerReason,
        featureFlags: nextFeatureFlags
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save AI settings");
    } finally {
      setSaving(false);
    }
  }

  if (loading || !state) {
    return <div className="text-sm text-muted-foreground">Loading AI settings...</div>;
  }

  const settings = state.settings;

  return (
    <div>
      <PageHeader title="AI Control Center" description="Configure provider readiness, automation toggles, fallback strategy and sensitive-action policy." />

      {error ? <p className="mb-4 text-sm text-red-600">{error}</p> : null}

      {!state.providerConfigured ? (
        <Card className="mb-4 border-amber-200 bg-amber-50">
          <CardContent className="flex items-start gap-2 p-4 text-sm text-amber-900">
            <AlertTriangle className="mt-0.5 h-4 w-4" />
            <div>
              <p className="font-medium">AI provider not configured</p>
              <p>{state.providerReason ?? "DeepSeek API key missing on server. AI features will fallback to rule mode."}</p>
            </div>
          </CardContent>
        </Card>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Provider & Model</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">Provider:</span>
              <Badge variant="outline">{settings.provider}</Badge>
            </div>
            <div className="space-y-2">
              <Label>Default Model</Label>
              <Input
                value={settings.modelDefault}
                disabled={!state.canManage || saving}
                onChange={(event) => setState({ ...state, settings: { ...settings, modelDefault: event.target.value } })}
              />
            </div>
            <div className="space-y-2">
              <Label>Reasoning Model</Label>
              <Input
                value={settings.modelReasoning}
                disabled={!state.canManage || saving}
                onChange={(event) => setState({ ...state, settings: { ...settings, modelReasoning: event.target.value } })}
              />
            </div>
            <div className="space-y-2">
              <Label>Fallback Mode</Label>
              <select
                className="h-9 w-full rounded-md border border-slate-200 bg-white px-2 text-sm"
                value={settings.fallbackMode}
                disabled={!state.canManage || saving}
                onChange={(event) =>
                  setState({
                    ...state,
                    settings: {
                      ...settings,
                      fallbackMode: event.target.value as OrgAiSettings["fallbackMode"]
                    }
                  })
                }
              >
                <option value="strict_provider_first">strict_provider_first</option>
                <option value="provider_then_rules">provider_then_rules</option>
                <option value="rules_only">rules_only</option>
              </select>
            </div>
            <Button
              onClick={() =>
                void save({
                  modelDefault: settings.modelDefault,
                  modelReasoning: settings.modelReasoning,
                  fallbackMode: settings.fallbackMode
                })
              }
              disabled={!state.canManage || saving}
            >
              <Save className="mr-2 h-4 w-4" />
              Save Model Settings
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Automation Policy</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="space-y-2">
              <Label>Max Daily AI Runs</Label>
              <Input
                type="number"
                value={settings.maxDailyAiRuns ?? 0}
                disabled={!state.canManage || saving}
                onChange={(event) =>
                  setState({
                    ...state,
                    settings: { ...settings, maxDailyAiRuns: Number(event.target.value || "0") }
                  })
                }
              />
            </div>
            <div className="space-y-2">
              <Label>Max Monthly AI Runs</Label>
              <Input
                type="number"
                value={settings.maxMonthlyAiRuns ?? 0}
                disabled={!state.canManage || saving}
                onChange={(event) =>
                  setState({
                    ...state,
                    settings: { ...settings, maxMonthlyAiRuns: Number(event.target.value || "0") }
                  })
                }
              />
            </div>

            <div className="space-y-3 pt-1">
              <div className="flex items-center justify-between">
                <Label>Auto Analysis</Label>
                <Switch
                  checked={settings.autoAnalysisEnabled}
                  disabled={!state.canManage || saving}
                  onCheckedChange={(checked) => setState({ ...state, settings: { ...settings, autoAnalysisEnabled: checked } })}
                />
              </div>
              <div className="flex items-center justify-between">
                <Label>Auto Plan</Label>
                <Switch
                  checked={settings.autoPlanEnabled}
                  disabled={!state.canManage || saving}
                  onCheckedChange={(checked) => setState({ ...state, settings: { ...settings, autoPlanEnabled: checked } })}
                />
              </div>
              <div className="flex items-center justify-between">
                <Label>Auto Morning Brief</Label>
                <Switch
                  checked={settings.autoBriefEnabled}
                  disabled={!state.canManage || saving}
                  onCheckedChange={(checked) => setState({ ...state, settings: { ...settings, autoBriefEnabled: checked } })}
                />
              </div>
              <div className="flex items-center justify-between">
                <Label>Auto Touchpoint Review</Label>
                <Switch
                  checked={settings.autoTouchpointReviewEnabled}
                  disabled={!state.canManage || saving}
                  onCheckedChange={(checked) => setState({ ...state, settings: { ...settings, autoTouchpointReviewEnabled: checked } })}
                />
              </div>
              <div className="flex items-center justify-between">
                <Label>Human Review Required</Label>
                <Switch
                  checked={settings.humanReviewRequiredForSensitiveActions}
                  disabled={!state.canManage || saving}
                  onCheckedChange={(checked) =>
                    setState({
                      ...state,
                      settings: {
                        ...settings,
                        humanReviewRequiredForSensitiveActions: checked
                      }
                    })
                  }
                />
              </div>
            </div>

            <Button
              onClick={() =>
                void save({
                  autoAnalysisEnabled: settings.autoAnalysisEnabled,
                  autoPlanEnabled: settings.autoPlanEnabled,
                  autoBriefEnabled: settings.autoBriefEnabled,
                  autoTouchpointReviewEnabled: settings.autoTouchpointReviewEnabled,
                  humanReviewRequiredForSensitiveActions: settings.humanReviewRequiredForSensitiveActions,
                  maxDailyAiRuns: settings.maxDailyAiRuns,
                  maxMonthlyAiRuns: settings.maxMonthlyAiRuns
                })
              }
              disabled={!state.canManage || saving}
            >
              <Save className="mr-2 h-4 w-4" />
              Save Automation Policy
            </Button>
          </CardContent>
        </Card>

        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>Feature Flags</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-2">
            {state.featureFlags.map((flag) => (
              <div key={flag.featureKey} className="flex items-center justify-between rounded-md border border-slate-200 px-3 py-2">
                <div>
                  <p className="text-sm font-medium text-slate-900">{flag.featureKey}</p>
                  <p className="text-xs text-muted-foreground">{featureMap[flag.featureKey] ? "enabled" : "disabled"}</p>
                </div>
                <Switch
                  checked={flag.isEnabled}
                  disabled={!state.canManage || saving}
                  onCheckedChange={(checked) => {
                    const next = state.featureFlags.map((item) =>
                      item.featureKey === flag.featureKey ? { ...item, isEnabled: checked } : item
                    );
                    setState({ ...state, featureFlags: next });
                    void save({ featureFlags: { [flag.featureKey]: checked } as Partial<Record<OrgFeatureKey, boolean>> });
                  }}
                />
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
