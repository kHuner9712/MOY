function normalizeOwnerKey(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  const raw = String(value).trim().toLowerCase();
  return raw || null;
}

export interface ImportOwnerCandidate {
  userId: string;
  displayName: string;
  email: string;
}

export function buildImportOwnerMap(
  rows: Array<{
    user_id: string;
    profile?: { display_name?: string | null } | null;
    email?: string | null;
  }>
): Map<string, ImportOwnerCandidate> {
  const map = new Map<string, ImportOwnerCandidate>();

  for (const row of rows) {
    const display = (row.profile?.display_name ?? "").trim();
    const email = (row.email ?? "").trim().toLowerCase();

    const candidate: ImportOwnerCandidate = {
      userId: row.user_id,
      displayName: display,
      email
    };

    if (display) {
      map.set(display.toLowerCase(), candidate);
    }
    if (email) {
      map.set(email, candidate);
    }
  }

  return map;
}

export function resolveImportOwnerId(value: unknown, ownerMap: Map<string, ImportOwnerCandidate>): string | null {
  const key = normalizeOwnerKey(value);
  if (!key) return null;
  return ownerMap.get(key)?.userId ?? null;
}
