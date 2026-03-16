export function clipMobileList(items: string[], limit: number): string[] {
  return items
    .map((item) => item.trim())
    .filter((item) => item.length > 0)
    .slice(0, Math.max(1, limit));
}

export function buildMobilePriorityPreview(items: Array<{ title: string }>, limit = 5): string[] {
  return clipMobileList(
    items.map((item) => item.title),
    limit
  );
}
