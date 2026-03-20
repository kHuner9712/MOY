/**
 * v1.2 Manager Desk Client Service
 */

import type { ManagerDeskResult, ManagerDeskFilters, ManagerIntervention } from "@/types/manager-desk";

class ManagerDeskClientService {
  private basePath = "/api/manager-desk";

  async getManagerDesk(filters?: ManagerDeskFilters): Promise<ManagerDeskResult> {
    const params = new URLSearchParams();
    if (filters?.ownerId) params.set("ownerId", filters.ownerId);
    if (filters?.riskLevel) params.set("riskLevel", filters.riskLevel);
    if (filters?.truthBand) params.set("truthBand", filters.truthBand);
    if (filters?.sortBy) params.set("sortBy", filters.sortBy);

    const url = `${this.basePath}${params.toString() ? `?${params}` : ""}`;
    const response = await fetch(url, { method: "GET", headers: { "Content-Type": "application/json" } });
    if (!response.ok) throw new Error("Failed to load manager desk");
    return response.json() as Promise<ManagerDeskResult>;
  }

  async createInterventionWorkItem(intervention: ManagerIntervention): Promise<{ workItemId: string; created: boolean }> {
    const response = await fetch(this.basePath, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ intervention })
    });
    if (!response.ok) throw new Error("Failed to create work item");
    return response.json() as Promise<{ workItemId: string; created: boolean }>;
  }

  async resolveIntervention(params: {
    interventionKey: string;
    resolution: "completed" | "dismissed";
    outcomeNote?: string;
    intervention: ManagerIntervention;
  }): Promise<{ id: string }> {
    const response = await fetch(this.basePath, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params)
    });
    if (!response.ok) throw new Error("Failed to resolve intervention");
    return response.json() as Promise<{ id: string }>;
  }
}

export const managerDeskClientService = new ManagerDeskClientService();
