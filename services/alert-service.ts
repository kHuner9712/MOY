import { createSupabaseBrowserClient } from "@/lib/supabase/client";
import { mapAlertRow } from "@/services/mappers";
import type { AlertItem } from "@/types/alert";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

export const alertService = {
  async listAll(): Promise<AlertItem[]> {
    const supabase = createSupabaseBrowserClient();
    const { data, error } = await supabase
      .from("alerts")
      .select("*, owner:profiles!alerts_owner_id_fkey(id, display_name), customer:customers!alerts_customer_id_fkey(id, company_name)")
      .order("created_at", { ascending: false });
    if (error) throw new Error(error.message);
    return (data ?? []).map((row) => mapAlertRow(row as never));
  },

  async listByOwner(ownerId: string): Promise<AlertItem[]> {
    const supabase = createSupabaseBrowserClient();
    const { data, error } = await supabase
      .from("alerts")
      .select("*, owner:profiles!alerts_owner_id_fkey(id, display_name), customer:customers!alerts_customer_id_fkey(id, company_name)")
      .eq("owner_id", ownerId)
      .order("created_at", { ascending: false });
    if (error) throw new Error(error.message);
    return (data ?? []).map((row) => mapAlertRow(row as never));
  },

  async updateStatus(alertId: string, status: AlertItem["status"]): Promise<void> {
    if (status === "resolved") {
      const response = await fetch(`/api/alerts/${alertId}/resolve`, {
        method: "POST"
      });
      const payload = (await response.json()) as ApiPayload<{ id: string; status: string }>;
      if (!response.ok || !payload.success) {
        throw new Error(payload.error ?? "标记解决失败");
      }
      return;
    }

    const supabase = createSupabaseBrowserClient();
    const patch: {
      status: AlertItem["status"];
      updated_at: string;
      resolved_at?: string | null;
    } = {
      status,
      updated_at: new Date().toISOString()
    };
    patch.resolved_at = null;

    const { error } = await (supabase.from("alerts") as any).update(patch).eq("id", alertId);
    if (error) throw new Error(error.message);
  },

  async runScan(): Promise<{
    runId: string;
    scannedCount: number;
    createdAlertCount: number;
    dedupedAlertCount: number;
    resolvedAlertCount: number;
  }> {
    const response = await fetch("/api/alerts/run-scan", {
      method: "POST"
    });

    const payload = (await response.json()) as ApiPayload<{
      runId: string;
      scannedCount: number;
      createdAlertCount: number;
      dedupedAlertCount: number;
      resolvedAlertCount: number;
    }>;

    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "运行漏单扫描失败");
    }

    return payload.data;
  },

  async convertToWorkItem(alertId: string): Promise<{ created: boolean; workItemId: string }> {
    const response = await fetch("/api/work-items/from-alert", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ alertId })
    });
    const payload = (await response.json()) as ApiPayload<{ workItem: { id: string }; created: boolean }>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Convert alert to work item failed");
    }
    return {
      created: payload.data.created,
      workItemId: payload.data.workItem.id
    };
  },

  async getAlertCoverage(alertIds: string[]): Promise<Record<string, boolean>> {
    if (alertIds.length === 0) return {};
    const response = await fetch(
      `/api/today/plan?sourceRefType=alert&sourceRefIds=${encodeURIComponent(alertIds.join(","))}`,
      { method: "GET" }
    );
    const payload = (await response.json()) as ApiPayload<{ coverage: Record<string, boolean> }>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to get alert coverage");
    }
    return payload.data.coverage;
  }
};
