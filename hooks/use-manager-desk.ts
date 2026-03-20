/**
 * v1.2 Manager Desk Hook
 */

import { useState, useEffect, useCallback } from "react";
import { managerDeskClientService } from "@/services/manager-desk-client-service";
import type { ManagerDeskResult, ManagerDeskFilters, ManagerIntervention } from "@/types/manager-desk";

export function useManagerDesk(filters?: ManagerDeskFilters) {
  const [data, setData] = useState<ManagerDeskResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await managerDeskClientService.getManagerDesk(filters);
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load manager desk");
    } finally {
      setLoading(false);
    }
  }, [filters?.ownerId, filters?.riskLevel, filters?.truthBand]);

  useEffect(() => {
    void load();
  }, [load]);

  const createWorkItem = useCallback(async (intervention: ManagerIntervention) => {
    setActionLoading(true);
    setActionError(null);
    try {
      await managerDeskClientService.createInterventionWorkItem(intervention);
      await load();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to create work item");
      throw err;
    } finally {
      setActionLoading(false);
    }
  }, [load]);

  const resolveIntervention = useCallback(async (params: {
    interventionKey: string;
    resolution: "completed" | "dismissed";
    outcomeNote?: string;
    intervention: ManagerIntervention;
  }) => {
    setActionLoading(true);
    setActionError(null);
    try {
      await managerDeskClientService.resolveIntervention(params);
      await load();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to resolve intervention");
      throw err;
    } finally {
      setActionLoading(false);
    }
  }, [load]);

  return {
    data,
    loading,
    error,
    reload: load,
    createWorkItem,
    resolveIntervention,
    actionLoading,
    actionError
  };
}
