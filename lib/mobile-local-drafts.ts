export interface LocalMobileDraft {
  localDraftId: string;
  draftType: "capture" | "outcome" | "email_draft" | "touchpoint_note";
  summary: string;
  payload: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
  syncStatus: "pending" | "synced" | "failed";
  lastError?: string | null;
}

const STORAGE_KEY = "moy_mobile_local_drafts_v1";

function safeParse(value: string | null): LocalMobileDraft[] {
  if (!value) return [];
  try {
    const parsed = JSON.parse(value) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item): item is LocalMobileDraft => {
      return Boolean(item && typeof item === "object" && typeof (item as LocalMobileDraft).localDraftId === "string");
    });
  } catch {
    return [];
  }
}

function persist(list: LocalMobileDraft[]): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
}

export function listLocalMobileDrafts(): LocalMobileDraft[] {
  if (typeof window === "undefined") return [];
  return safeParse(window.localStorage.getItem(STORAGE_KEY)).sort((a, b) => +new Date(b.updatedAt) - +new Date(a.updatedAt));
}

export function saveLocalMobileDraft(input: Omit<LocalMobileDraft, "localDraftId" | "createdAt" | "updatedAt" | "syncStatus"> & { localDraftId?: string }): LocalMobileDraft {
  const now = new Date().toISOString();
  const list = listLocalMobileDrafts();
  const localDraftId = input.localDraftId ?? `local-${crypto.randomUUID()}`;
  const existing = list.find((item) => item.localDraftId === localDraftId);
  const record: LocalMobileDraft = {
    localDraftId,
    draftType: input.draftType,
    summary: input.summary,
    payload: input.payload,
    createdAt: existing?.createdAt ?? now,
    updatedAt: now,
    syncStatus: "pending",
    lastError: null
  };
  const next = [record, ...list.filter((item) => item.localDraftId !== localDraftId)];
  persist(next);
  return record;
}

export function markLocalMobileDraftSynced(localDraftId: string): void {
  const list = listLocalMobileDrafts();
  const next = list.map((item) =>
    item.localDraftId === localDraftId
      ? {
          ...item,
          syncStatus: "synced" as const,
          updatedAt: new Date().toISOString(),
          lastError: null
        }
      : item
  );
  persist(next);
}

export function markLocalMobileDraftFailed(localDraftId: string, error: string): void {
  const list = listLocalMobileDrafts();
  const next = list.map((item) =>
    item.localDraftId === localDraftId
      ? {
          ...item,
          syncStatus: "failed" as const,
          updatedAt: new Date().toISOString(),
          lastError: error
        }
      : item
  );
  persist(next);
}

export function removeLocalMobileDraft(localDraftId: string): void {
  const list = listLocalMobileDrafts();
  persist(list.filter((item) => item.localDraftId !== localDraftId));
}
