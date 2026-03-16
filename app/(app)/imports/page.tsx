"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { IndustryTemplateBanner } from "@/components/shared/industry-template-banner";
import { PageHeader } from "@/components/shared/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { formatDateTime } from "@/lib/format";
import { importClientService } from "@/services/import-client-service";
import { useIndustryTemplate } from "@/hooks/use-industry-template";
import type { Database } from "@/types/database";
import type { ImportJob, ImportTemplate } from "@/types/import";

const CUSTOMER_SAMPLE = `customer_name,company_name,contact_name,phone,email,owner,stage,risk_level,next_followup_at,tags,source_channel\n` +
  `\u674e\u603b,\u661f\u6cb3\u5236\u9020,\u674e\u603b,13800138000,li@example.com,Lin Yue,proposal,high,2026-03-20,high_value;budget_sensitive,website`;

const OPPORTUNITY_SAMPLE = `title,company_name,owner,amount,stage,risk_level,expected_close_date,notes\n` +
  `\u667a\u80fd\u5ba2\u670d\u4e00\u671f,\u661f\u6cb3\u5236\u9020,Lin Yue,380000,proposal,medium,2026-04-30,\u9700\u8981\u62c6\u5206\u62a5\u4ef7`;

function downloadTextFile(fileName: string, text: string): void {
  const blob = new Blob([text], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  anchor.click();
  URL.revokeObjectURL(url);
}

function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(typeof reader.result === "string" ? reader.result : "");
    reader.onerror = () => reject(new Error("read_file_text_failed"));
    reader.readAsText(file, "utf-8");
  });
}

function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      const base64 = result.includes(",") ? result.split(",")[1] ?? "" : result;
      resolve(base64);
    };
    reader.onerror = () => reject(new Error("read_file_base64_failed"));
    reader.readAsDataURL(file);
  });
}

function statusTone(status: ImportJob["jobStatus"]): "default" | "secondary" | "destructive" | "outline" {
  if (status === "completed") return "default";
  if (status === "failed" || status === "cancelled") return "destructive";
  if (status === "preview_ready" || status === "mapping") return "secondary";
  return "outline";
}

export default function ImportsPage(): JSX.Element {
  const { user } = useAuth();
  const router = useRouter();
  const { data: templateContext } = useIndustryTemplate(true);

  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [jobs, setJobs] = useState<ImportJob[]>([]);
  const [templates, setTemplates] = useState<ImportTemplate[]>([]);
  const [canWrite, setCanWrite] = useState(false);

  const [importType, setImportType] = useState<Database["public"]["Enums"]["import_type"]>("customers");
  const [sourceType, setSourceType] = useState<Database["public"]["Enums"]["import_source_type"]>("csv");
  const [fileName, setFileName] = useState("customers-import.csv");
  const [manualText, setManualText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [enableAiAssist, setEnableAiAssist] = useState(true);

  const canManage = user?.role === "manager";

  const pendingCount = useMemo(() => jobs.filter((item) => item.jobStatus !== "completed" && item.jobStatus !== "failed" && item.jobStatus !== "cancelled").length, [jobs]);
  const templateCustomerStages = (templateContext?.template?.templatePayload.customer_stages as string[] | undefined) ?? [];
  const templateOpportunityStages = (templateContext?.template?.templatePayload.opportunity_stages as string[] | undefined) ?? [];

  async function load(): Promise<void> {
    try {
      setLoading(true);
      setError(null);

      const [jobPayload, templatePayload] = await Promise.all([
        importClientService.listJobs(40),
        importClientService.listTemplates()
      ]);

      setJobs(jobPayload.jobs);
      setCanWrite(jobPayload.canWrite);
      setTemplates(templatePayload.templates);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "load_imports_failed");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!canManage) {
      setLoading(false);
      return;
    }
    void load();
  }, [canManage]);

  async function startImport(): Promise<void> {
    if (!canManage) return;

    setSubmitting(true);
    setError(null);
    setMessage(null);

    try {
      const created = await importClientService.createJob({
        importType,
        sourceType,
        fileName: fileName.trim() || `import-${Date.now()}.csv`
      });

      const jobId = created.job.id;

      let fileText: string | undefined;
      let fileBase64: string | undefined;

      if (sourceType === "manual_table") {
        if (!manualText.trim()) throw new Error("Please paste table text for manual_table source");
        fileText = manualText;
      } else if (sourceType === "csv") {
        if (!file) throw new Error("Please choose a CSV file first");
        fileText = await readFileAsText(file);
      } else if (sourceType === "xlsx") {
        if (!file) throw new Error("Please choose an XLSX file first");
        fileBase64 = await readFileAsBase64(file);
      }

      await importClientService.upload({
        jobId,
        sourceType,
        fileText,
        fileBase64
      });

      await importClientService.detectMapping({
        jobId,
        enableAiAssist
      });

      setMessage("Import file parsed. Mapping suggestions ready.");
      await load();
      router.push(`/imports/${jobId}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "start_import_failed");
    } finally {
      setSubmitting(false);
    }
  }

  if (!canManage) {
    return <div className="text-sm text-muted-foreground">Import center is available for manager/admin role only.</div>;
  }

  if (loading) {
    return <div className="text-sm text-muted-foreground">Loading import center...</div>;
  }

  return (
    <div>
      <PageHeader
        title="Import Center"
        description="Upload CSV/XLSX, map columns, validate, dedupe, and import with audit trail."
        action={
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => void load()} disabled={loading || submitting}>
              Refresh
            </Button>
            <Button asChild variant="outline">
              <Link href="/settings/onboarding">Back To Onboarding</Link>
            </Button>
          </div>
        }
      />

      {error ? <p className="mb-3 text-sm text-rose-600">{error}</p> : null}
      {message ? <p className="mb-3 text-sm text-emerald-700">{message}</p> : null}
      <IndustryTemplateBanner className="mb-4" compact />

      <section className="mb-4 grid gap-4 xl:grid-cols-[1.1fr_1.9fr]">
        <Card>
          <CardHeader>
            <CardTitle>Create Import Job</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid gap-1">
              <Label>Import Type</Label>
              <Select value={importType} onValueChange={(value) => setImportType(value as Database["public"]["Enums"]["import_type"])}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="customers">customers</SelectItem>
                  <SelectItem value="opportunities">opportunities</SelectItem>
                  <SelectItem value="followups">followups</SelectItem>
                  <SelectItem value="mixed">mixed</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="grid gap-1">
              <Label>Source Type</Label>
              <Select value={sourceType} onValueChange={(value) => setSourceType(value as Database["public"]["Enums"]["import_source_type"])}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="csv">csv</SelectItem>
                  <SelectItem value="xlsx">xlsx</SelectItem>
                  <SelectItem value="manual_table">manual_table</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="grid gap-1">
              <Label>File Name</Label>
              <Input value={fileName} onChange={(event) => setFileName(event.target.value)} placeholder="customers-import.csv" />
            </div>

            {sourceType === "manual_table" ? (
              <div className="grid gap-1">
                <Label>Paste CSV/Table Text</Label>
                <Textarea
                  value={manualText}
                  onChange={(event) => setManualText(event.target.value)}
                  placeholder="Paste header + rows, comma-separated"
                  className="min-h-[150px]"
                />
              </div>
            ) : (
              <div className="grid gap-1">
                <Label>Upload File</Label>
                <Input
                  type="file"
                  accept={sourceType === "csv" ? ".csv,text/csv" : ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
                  onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                />
              </div>
            )}

            <div className="flex items-center justify-between rounded-md border border-slate-200 px-3 py-2 text-xs">
              <span>AI mapping assist</span>
              <Button size="sm" variant={enableAiAssist ? "default" : "outline"} onClick={() => setEnableAiAssist((prev) => !prev)}>
                {enableAiAssist ? "enabled" : "disabled"}
              </Button>
            </div>

            <Button className="w-full" disabled={submitting || !canWrite} onClick={() => void startImport()}>
              {submitting ? "Parsing..." : "Create & Parse"}
            </Button>
            {!canWrite ? <p className="text-xs text-muted-foreground">Current account is read-only for imports. Ask owner/admin for write access.</p> : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Import Workflow</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm text-slate-700">
            <p>1. Upload / paste source table.</p>
            <p>2. Detect mapping (rule-first + optional AI assist).</p>
            <p>3. Validate + normalization preview.</p>
            <p>4. Review dedupe candidates (create / merge / skip).</p>
            <p>5. Execute import with partial-success support and audit trail.</p>
            <p>6. Trigger post-import bootstrap suggestions.</p>

            {templateContext?.template ? (
              <div className="rounded-md border border-sky-200 bg-sky-50 p-3 text-xs text-sky-900">
                <p className="font-semibold">Template-aware mapping hints ({templateContext.template.displayName})</p>
                <p className="mt-1">customer stages: {templateCustomerStages.join(" / ") || "-"}</p>
                <p className="mt-1">opportunity stages: {templateOpportunityStages.join(" / ") || "-"}</p>
                <p className="mt-1">Tip: normalize source stage values to these options before final import execution.</p>
              </div>
            ) : null}

            <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-xs">
              <p>Pending jobs: {pendingCount}</p>
              <p>Total jobs: {jobs.length}</p>
              <p>Templates: {templates.length}</p>
            </div>

            <div className="flex flex-wrap gap-2">
              <Button variant="outline" size="sm" onClick={() => downloadTextFile("customers-template.csv", CUSTOMER_SAMPLE)}>
                Download Customers Template
              </Button>
              <Button variant="outline" size="sm" onClick={() => downloadTextFile("opportunities-template.csv", OPPORTUNITY_SAMPLE)}>
                Download Opportunities Template
              </Button>
            </div>
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.6fr_1.4fr]">
        <Card>
          <CardHeader>
            <CardTitle>Recent Import Jobs</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {jobs.length === 0 ? <p className="text-sm text-muted-foreground">No import jobs yet.</p> : null}
            {jobs.map((job) => (
              <Link key={job.id} href={`/imports/${job.id}`} className="block rounded-md border border-slate-200 p-3 transition hover:border-sky-300 hover:bg-sky-50/40">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{job.fileName}</p>
                  <Badge variant={statusTone(job.jobStatus)}>{job.jobStatus}</Badge>
                </div>
                <p className="text-xs text-muted-foreground">
                  type={job.importType} | source={job.sourceType} | created={formatDateTime(job.createdAt)}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  total={job.totalRows}, valid={job.validRows}, invalid={job.invalidRows}, duplicate={job.duplicateRows}, imported={job.importedRows}
                </p>
                <p className="mt-1 text-xs text-slate-700">{job.summary ?? "No summary yet."}</p>
              </Link>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Saved Templates</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {templates.length === 0 ? <p className="text-sm text-muted-foreground">No template saved yet.</p> : null}
            {templates.map((template) => (
              <div key={template.id} className="rounded-md border border-slate-200 p-3">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{template.templateName}</p>
                  {template.isDefault ? <Badge>default</Badge> : <Badge variant="outline">saved</Badge>}
                </div>
                <p className="text-xs text-muted-foreground">type={template.importType}</p>
                <p className="text-xs text-muted-foreground">updated={formatDateTime(template.updatedAt)}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      </section>
    </div>
  );
}

