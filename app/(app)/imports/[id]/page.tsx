"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { PageHeader } from "@/components/shared/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { formatDateTime } from "@/lib/format";
import { importClientService } from "@/services/import-client-service";
import type { Database } from "@/types/database";
import type { DedupeMatchGroup, ImportAuditEvent, ImportJob, ImportJobColumn, ImportJobRow } from "@/types/import";

const TARGET_ENTITY_OPTIONS: Array<Database["public"]["Enums"]["import_entity_type"]> = ["customer", "opportunity", "followup", "mixed"];
const DEDUPE_ACTION_OPTIONS: Array<Database["public"]["Enums"]["dedupe_resolution_action"]> = ["create_new", "merge", "skip"];

function statusTone(status: ImportJob["jobStatus"]): "default" | "secondary" | "destructive" | "outline" {
  if (status === "completed") return "default";
  if (status === "failed" || status === "cancelled") return "destructive";
  if (status === "preview_ready" || status === "mapping") return "secondary";
  return "outline";
}

export default function ImportJobDetailPage(): JSX.Element {
  const params = useParams<{ id: string }>();
  const jobId = params?.id;

  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const [job, setJob] = useState<ImportJob | null>(null);
  const [columns, setColumns] = useState<ImportJobColumn[]>([]);
  const [rows, setRows] = useState<ImportJobRow[]>([]);
  const [dedupeGroups, setDedupeGroups] = useState<DedupeMatchGroup[]>([]);
  const [auditEvents, setAuditEvents] = useState<ImportAuditEvent[]>([]);
  const [canWrite, setCanWrite] = useState(false);

  const [columnDraft, setColumnDraft] = useState<
    Record<
      string,
      {
        mappedTargetEntity: Database["public"]["Enums"]["import_entity_type"] | null;
        mappedTargetField: string | null;
      }
    >
  >({});

  const [groupActionDraft, setGroupActionDraft] = useState<Record<string, Database["public"]["Enums"]["dedupe_resolution_action"]>>({});

  const summary = useMemo(() => {
    if (!job) return null;
    return {
      totalRows: job.totalRows,
      validRows: job.validRows,
      invalidRows: job.invalidRows,
      duplicateRows: job.duplicateRows,
      importedRows: job.importedRows,
      mergedRows: job.mergedRows,
      skippedRows: job.skippedRows,
      errorRows: job.errorRows
    };
  }, [job]);

  const load = useCallback(async (): Promise<void> => {
    if (!jobId) return;

    try {
      setLoading(true);
      setError(null);

      const [detail, rowsPayload, auditPayload] = await Promise.all([
        importClientService.getJob(jobId),
        importClientService.getRows(jobId, 150, 0),
        importClientService.getAudit(jobId, 120)
      ]);

      setJob(detail.job);
      setColumns(detail.columns);
      setRows(rowsPayload.rows);
      setDedupeGroups(detail.dedupeGroups);
      setAuditEvents(auditPayload.events);
      setCanWrite(detail.canWrite);

      const nextDraft: Record<string, { mappedTargetEntity: Database["public"]["Enums"]["import_entity_type"] | null; mappedTargetField: string | null }> = {};
      for (const column of detail.columns) {
        nextDraft[column.id] = {
          mappedTargetEntity: column.mappedTargetEntity,
          mappedTargetField: column.mappedTargetField
        };
      }
      setColumnDraft(nextDraft);

      const nextGroupDraft: Record<string, Database["public"]["Enums"]["dedupe_resolution_action"]> = {};
      for (const group of detail.dedupeGroups) {
        nextGroupDraft[group.id] = (group.resolutionAction ?? "merge") as Database["public"]["Enums"]["dedupe_resolution_action"];
      }
      setGroupActionDraft(nextGroupDraft);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "load_import_job_failed");
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function runAction(key: string, action: () => Promise<void>): Promise<void> {
    setActionLoading(key);
    setError(null);
    setMessage(null);

    try {
      await action();
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "import_action_failed");
    } finally {
      setActionLoading(null);
    }
  }

  if (!jobId) {
    return <div className="text-sm text-muted-foreground">Missing import job id.</div>;
  }

  if (loading || !job) {
    return <div className="text-sm text-muted-foreground">Loading import job...</div>;
  }

  return (
    <div>
      <PageHeader
        title={`Import Job ${job.fileName}`}
        description="Mapping, validation, dedupe, execution and audit in one place."
        action={
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => void load()} disabled={loading || Boolean(actionLoading)}>
              Refresh
            </Button>
            <Button asChild variant="outline">
              <Link href="/imports">Back To Import Center</Link>
            </Button>
          </div>
        }
      />

      {error ? <p className="mb-3 text-sm text-rose-600">{error}</p> : null}
      {message ? <p className="mb-3 text-sm text-emerald-700">{message}</p> : null}

      <section className="mb-4 grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Status</CardTitle>
          </CardHeader>
          <CardContent>
            <Badge variant={statusTone(job.jobStatus)}>{job.jobStatus}</Badge>
            <p className="mt-2 text-xs text-muted-foreground">updated: {formatDateTime(job.updatedAt)}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Rows</CardTitle>
          </CardHeader>
          <CardContent className="text-sm">
            <p>total: {summary?.totalRows ?? 0}</p>
            <p>valid: {summary?.validRows ?? 0}</p>
            <p>invalid: {summary?.invalidRows ?? 0}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Dedupe</CardTitle>
          </CardHeader>
          <CardContent className="text-sm">
            <p>candidate rows: {summary?.duplicateRows ?? 0}</p>
            <p>groups: {dedupeGroups.length}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Execution</CardTitle>
          </CardHeader>
          <CardContent className="text-sm">
            <p>imported: {summary?.importedRows ?? 0}</p>
            <p>merged: {summary?.mergedRows ?? 0}</p>
            <p>failed: {summary?.errorRows ?? 0}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Audit</CardTitle>
          </CardHeader>
          <CardContent className="text-sm">
            <p>events: {auditEvents.length}</p>
            <p className="text-xs text-muted-foreground">created: {formatDateTime(job.createdAt)}</p>
          </CardContent>
        </Card>
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-[1.1fr_1.9fr]">
        <Card>
          <CardHeader>
            <CardTitle>Import Actions</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <Button
              className="w-full"
              variant="outline"
              disabled={Boolean(actionLoading) || !canWrite}
              onClick={() =>
                void runAction("detect", async () => {
                  const result = await importClientService.detectMapping({
                    jobId,
                    enableAiAssist: true
                  });
                  setMessage(result.usedFallback ? `Mapping detected with fallback: ${result.fallbackReason ?? "unknown"}` : "Mapping detected.");
                })
              }
            >
              Detect Mapping
            </Button>
            <Button
              className="w-full"
              variant="outline"
              disabled={Boolean(actionLoading) || !canWrite}
              onClick={() =>
                void runAction("saveMapping", async () => {
                  await importClientService.saveMapping({
                    jobId,
                    mapping: columns.map((column) => ({
                      columnId: column.id,
                      mappedTargetEntity: columnDraft[column.id]?.mappedTargetEntity ?? null,
                      mappedTargetField: columnDraft[column.id]?.mappedTargetField ?? null
                    }))
                  });
                  setMessage("Mapping saved.");
                })
              }
            >
              Save Mapping
            </Button>
            <Button
              className="w-full"
              variant="outline"
              disabled={Boolean(actionLoading) || !canWrite}
              onClick={() =>
                void runAction("validate", async () => {
                  const result = await importClientService.validate(jobId);
                  setMessage(`Validation done: valid=${result.validRows}, invalid=${result.invalidRows}, duplicate=${result.duplicateRows}`);
                })
              }
            >
              Run Validation
            </Button>
            <Button
              className="w-full"
              variant="outline"
              disabled={Boolean(actionLoading) || !canWrite}
              onClick={() =>
                void runAction("dedupe", async () => {
                  const result = await importClientService.dedupe(jobId);
                  setMessage(`Dedupe done: ${result.groups.length} groups.`);
                })
              }
            >
              Run Dedupe
            </Button>
            <Button
              className="w-full"
              disabled={Boolean(actionLoading) || !canWrite}
              onClick={() =>
                void runAction("execute", async () => {
                  const result = await importClientService.execute({
                    jobId,
                    runReviewSummary: true
                  });
                  setMessage(
                    `Import completed: imported=${result.counters.importedRows}, merged=${result.counters.mergedRows}, skipped=${result.counters.skippedRows}, failed=${result.counters.errorRows}`
                  );
                })
              }
            >
              Execute Import
            </Button>
            {!canWrite ? <p className="text-xs text-muted-foreground">Read-only mode: owner/admin role is required to run import actions.</p> : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Column Mapping</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {columns.length === 0 ? <p className="text-sm text-muted-foreground">No parsed columns yet.</p> : null}
            {columns.map((column) => (
              <div key={column.id} className="rounded-md border border-slate-200 p-3">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{column.sourceColumnName}</p>
                  <Badge variant="outline">confidence {(column.mappingConfidence ?? 0).toFixed(2)}</Badge>
                </div>
                <div className="grid gap-2 md:grid-cols-2">
                  <div className="grid gap-1">
                    <Label>Target Entity</Label>
                    <Select
                      value={columnDraft[column.id]?.mappedTargetEntity ?? "none"}
                      onValueChange={(value) =>
                        setColumnDraft((prev) => ({
                          ...prev,
                          [column.id]: {
                            mappedTargetEntity: value === "none" ? null : (value as Database["public"]["Enums"]["import_entity_type"]),
                            mappedTargetField: prev[column.id]?.mappedTargetField ?? null
                          }
                        }))
                      }
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">none</SelectItem>
                        {TARGET_ENTITY_OPTIONS.map((item) => (
                          <SelectItem key={item} value={item}>
                            {item}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="grid gap-1">
                    <Label>Target Field</Label>
                    <Input
                      value={columnDraft[column.id]?.mappedTargetField ?? ""}
                      onChange={(event) =>
                        setColumnDraft((prev) => ({
                          ...prev,
                          [column.id]: {
                            mappedTargetEntity: prev[column.id]?.mappedTargetEntity ?? null,
                            mappedTargetField: event.target.value.trim() || null
                          }
                        }))
                      }
                      placeholder="e.g. company_name / amount / summary"
                    />
                  </div>
                </div>
                <p className="mt-2 text-xs text-muted-foreground">detected={column.detectedType ?? "unknown"}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-[1.2fr_1.8fr]">
        <Card>
          <CardHeader>
            <CardTitle>Dedupe Resolution</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {dedupeGroups.length === 0 ? <p className="text-sm text-muted-foreground">No dedupe groups.</p> : null}
            {dedupeGroups.map((group) => (
              <div key={group.id} className="rounded-md border border-slate-200 p-3">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{group.entityType}</p>
                  <Badge variant="outline">score {group.confidenceScore.toFixed(2)}</Badge>
                </div>
                <p className="text-xs text-muted-foreground">rows: {group.sourceRowIds.length} | existing: {group.existingEntityIds.length}</p>
                <p className="mt-1 text-xs text-muted-foreground">reason: {group.matchReason}</p>
                <div className="mt-2 flex items-center gap-2">
                  <Select
                    value={groupActionDraft[group.id] ?? "merge"}
                    onValueChange={(value) =>
                      setGroupActionDraft((prev) => ({
                        ...prev,
                        [group.id]: value as Database["public"]["Enums"]["dedupe_resolution_action"]
                      }))
                    }
                  >
                    <SelectTrigger className="w-[180px]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {DEDUPE_ACTION_OPTIONS.map((action) => (
                        <SelectItem key={action} value={action}>
                          {action}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
            ))}

            {dedupeGroups.length > 0 ? (
              <Button
                className="w-full"
                variant="outline"
                disabled={Boolean(actionLoading) || !canWrite}
                onClick={() =>
                  void runAction("resolveDedupe", async () => {
                    await importClientService.applyDedupeResolution({
                      jobId,
                      resolutions: dedupeGroups.map((group) => ({
                        groupId: group.id,
                        action: groupActionDraft[group.id] ?? "merge"
                      }))
                    });
                    setMessage("Dedupe resolutions applied.");
                  })
                }
              >
                Apply Dedupe Resolutions
              </Button>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Row Preview (Top 150)</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {rows.length === 0 ? <p className="text-sm text-muted-foreground">No rows.</p> : null}
            {rows.map((row) => (
              <div key={row.id} className="rounded-md border border-slate-200 p-3">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">row #{row.sourceRowNo}</p>
                  <Badge variant="outline">{row.rowStatus}</Badge>
                </div>
                {row.validationErrors.length > 0 ? (
                  <p className="text-xs text-rose-600">errors: {row.validationErrors.join(" | ")}</p>
                ) : (
                  <p className="text-xs text-muted-foreground">no validation errors</p>
                )}
                {row.duplicateCandidates.length > 0 ? (
                  <p className="mt-1 text-xs text-amber-700">duplicate candidates: {row.duplicateCandidates.length}</p>
                ) : null}
                <p className="mt-1 text-xs text-muted-foreground">merge_resolution: {row.mergeResolution ?? "-"}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      </section>

      <section>
        <Card>
          <CardHeader>
            <CardTitle>Import Audit Events</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {auditEvents.length === 0 ? <p className="text-sm text-muted-foreground">No audit events yet.</p> : null}
            {auditEvents.map((event) => (
              <div key={event.id} className="rounded-md border border-slate-200 p-3">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{event.eventType}</p>
                  <p className="text-xs text-muted-foreground">{formatDateTime(event.createdAt)}</p>
                </div>
                <p className="text-xs text-slate-700">{event.eventSummary}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
