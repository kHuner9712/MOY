import type { BriefingHubView, ContentDraft, MorningBrief, PrepCard } from "@/types/preparation";

function timeValue(isoText: string): number {
  return new Date(isoText).getTime();
}

export function sortPrepCardsByRecent(prepCards: PrepCard[]): PrepCard[] {
  return [...prepCards].sort((a, b) => timeValue(b.updatedAt) - timeValue(a.updatedAt));
}

export function sortContentDraftsByRecent(contentDrafts: ContentDraft[]): ContentDraft[] {
  return [...contentDrafts].sort((a, b) => timeValue(b.updatedAt) - timeValue(a.updatedAt));
}

export function mapPrepCoverageByWorkItem(prepCards: PrepCard[], workItemIds: string[]): Record<string, PrepCard> {
  const target = new Set(workItemIds);
  const byWorkItem: Record<string, PrepCard> = {};
  for (const card of sortPrepCardsByRecent(prepCards)) {
    if (!card.workItemId) continue;
    if (!target.has(card.workItemId)) continue;
    if (!byWorkItem[card.workItemId]) {
      byWorkItem[card.workItemId] = card;
    }
  }
  return byWorkItem;
}

export function mapDraftCoverageByWorkItem(contentDrafts: ContentDraft[], workItemIds: string[]): Record<string, ContentDraft[]> {
  const target = new Set(workItemIds);
  const byWorkItem: Record<string, ContentDraft[]> = {};
  for (const draft of sortContentDraftsByRecent(contentDrafts)) {
    if (!draft.workItemId) continue;
    if (!target.has(draft.workItemId)) continue;
    if (!byWorkItem[draft.workItemId]) {
      byWorkItem[draft.workItemId] = [];
    }
    byWorkItem[draft.workItemId].push(draft);
  }
  return byWorkItem;
}

export function buildBriefingHubView(input: {
  morningBrief: MorningBrief | null;
  prepCards: PrepCard[];
  contentDrafts: ContentDraft[];
}): BriefingHubView {
  return {
    morningBrief: input.morningBrief,
    prepCards: sortPrepCardsByRecent(input.prepCards),
    contentDrafts: sortContentDraftsByRecent(input.contentDrafts)
  };
}

