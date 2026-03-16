"use client";

import { useAuth } from "@/components/auth/auth-provider";
import { alertService } from "@/services/alert-service";
import { communicationInputService } from "@/services/communication-input-service";
import { customerService } from "@/services/customer-service";
import { followupService } from "@/services/followup-service";
import { opportunityService } from "@/services/opportunity-service";
import { reportClientService } from "@/services/report-client-service";
import type { AlertItem } from "@/types/alert";
import type { CaptureConfirmInput, CaptureConfirmResult, CaptureExtractInput, CaptureExtractResult, CommunicationInputItem } from "@/types/communication";
import type { Customer } from "@/types/customer";
import type { FollowupCreateResult, FollowupInput, FollowupRecord } from "@/types/followup";
import type { Opportunity } from "@/types/opportunity";
import type { GenerateReportInput, GeneratedReport } from "@/types/report";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

interface AppDataContextValue {
  customers: Customer[];
  followups: FollowupRecord[];
  opportunities: Opportunity[];
  alerts: AlertItem[];
  communicationInputs: CommunicationInputItem[];
  reports: GeneratedReport[];
  loading: boolean;
  error: string | null;
  refreshAll: () => Promise<void>;
  getCustomerById: (customerId: string) => Customer | undefined;
  getFollowupsByCustomerId: (customerId: string) => FollowupRecord[];
  getCommunicationInputsByCustomerId: (customerId: string) => CommunicationInputItem[];
  addFollowup: (input: FollowupInput) => Promise<FollowupCreateResult>;
  updateAlertStatus: (alertId: string, status: AlertItem["status"]) => Promise<void>;
  runAlertScan: () => Promise<{ createdAlertCount: number; dedupedAlertCount: number; resolvedAlertCount: number; scannedCount: number }>;
  extractCommunicationInput: (input: CaptureExtractInput) => Promise<CaptureExtractResult>;
  confirmCommunicationInput: (input: CaptureConfirmInput) => Promise<CaptureConfirmResult>;
  generateReport: (input: GenerateReportInput) => Promise<GeneratedReport>;
  getReportById: (reportId: string) => Promise<GeneratedReport>;
}

const AppDataContext = createContext<AppDataContextValue | null>(null);

export function AppDataProvider({ children }: { children: React.ReactNode }): JSX.Element {
  const { user } = useAuth();
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [followups, setFollowups] = useState<FollowupRecord[]>([]);
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [communicationInputs, setCommunicationInputs] = useState<CommunicationInputItem[]>([]);
  const [reports, setReports] = useState<GeneratedReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refreshAll = useCallback(async (): Promise<void> => {
    if (!user) {
      setCustomers([]);
      setFollowups([]);
      setOpportunities([]);
      setAlerts([]);
      setCommunicationInputs([]);
      setReports([]);
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const [nextCustomers, nextFollowups, nextOpportunities, nextAlerts, nextCommunicationInputs, nextReports] = await Promise.all([
        customerService.listCustomers(),
        followupService.listAll(),
        opportunityService.list(),
        alertService.listAll(),
        communicationInputService.listAll(),
        reportClientService.list({ limit: 60 })
      ]);

      setCustomers(nextCustomers);
      setFollowups(nextFollowups);
      setOpportunities(nextOpportunities);
      setAlerts(nextAlerts);
      setCommunicationInputs(nextCommunicationInputs);
      setReports(nextReports);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to load workspace data.");
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    void refreshAll();
  }, [refreshAll]);

  const getCustomerById = useCallback((customerId: string): Customer | undefined => customers.find((item) => item.id === customerId), [customers]);

  const getFollowupsByCustomerId = useCallback(
    (customerId: string): FollowupRecord[] => {
      return followups
        .filter((item) => item.customerId === customerId)
        .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());
    },
    [followups]
  );

  const getCommunicationInputsByCustomerId = useCallback(
    (customerId: string): CommunicationInputItem[] => {
      return communicationInputs
        .filter((item) => item.customerId === customerId)
        .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());
    },
    [communicationInputs]
  );

  const addFollowup = useCallback(
    async (input: FollowupInput): Promise<FollowupCreateResult> => {
      const result = await followupService.create(input);
      await refreshAll();
      return result;
    },
    [refreshAll]
  );

  const updateAlertStatus = useCallback(
    async (alertId: string, status: AlertItem["status"]): Promise<void> => {
      await alertService.updateStatus(alertId, status);
      setAlerts((prev) =>
        prev.map((item) =>
          item.id === alertId
            ? {
                ...item,
                status
              }
            : item
        )
      );
    },
    []
  );

  const runAlertScan = useCallback(async (): Promise<{
    createdAlertCount: number;
    dedupedAlertCount: number;
    resolvedAlertCount: number;
    scannedCount: number;
  }> => {
    const result = await alertService.runScan();
    await refreshAll();
    return result;
  }, [refreshAll]);

  const extractCommunicationInput = useCallback(
    async (input: CaptureExtractInput): Promise<CaptureExtractResult> => {
      const result = await communicationInputService.extract(input);
      await refreshAll();
      return result;
    },
    [refreshAll]
  );

  const confirmCommunicationInput = useCallback(
    async (input: CaptureConfirmInput): Promise<CaptureConfirmResult> => {
      const result = await communicationInputService.confirm(input);
      await refreshAll();
      return result;
    },
    [refreshAll]
  );

  const generateReport = useCallback(
    async (input: GenerateReportInput): Promise<GeneratedReport> => {
      const report = await reportClientService.generate(input);
      await refreshAll();
      return report;
    },
    [refreshAll]
  );

  const getReportById = useCallback(async (reportId: string): Promise<GeneratedReport> => {
    return reportClientService.getById(reportId);
  }, []);

  const value = useMemo<AppDataContextValue>(
    () => ({
      customers,
      followups,
      opportunities,
      alerts,
      communicationInputs,
      reports,
      loading,
      error,
      refreshAll,
      getCustomerById,
      getFollowupsByCustomerId,
      getCommunicationInputsByCustomerId,
      addFollowup,
      updateAlertStatus,
      runAlertScan,
      extractCommunicationInput,
      confirmCommunicationInput,
      generateReport,
      getReportById
    }),
    [
      customers,
      followups,
      opportunities,
      alerts,
      communicationInputs,
      reports,
      loading,
      error,
      refreshAll,
      getCustomerById,
      getFollowupsByCustomerId,
      getCommunicationInputsByCustomerId,
      addFollowup,
      updateAlertStatus,
      runAlertScan,
      extractCommunicationInput,
      confirmCommunicationInput,
      generateReport,
      getReportById
    ]
  );

  return <AppDataContext.Provider value={value}>{children}</AppDataContext.Provider>;
}

export function useAppData(): AppDataContextValue {
  const context = useContext(AppDataContext);
  if (!context) throw new Error("useAppData must be used within AppDataProvider");
  return context;
}
