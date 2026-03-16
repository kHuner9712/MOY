interface ExistingCustomerLite {
  id: string;
  companyName: string;
  contactName: string;
  phone: string | null;
  email: string | null;
  ownerId: string;
}

interface ExistingOpportunityLite {
  id: string;
  customerId: string;
  title: string;
  amount: number;
  stage: string;
  ownerId: string;
}

function normalize(value: string | null | undefined): string {
  return (value ?? "").trim().toLowerCase();
}

function similarity(a: string, b: string): number {
  if (!a || !b) return 0;
  if (a === b) return 1;
  if (a.includes(b) || b.includes(a)) return 0.8;
  return 0;
}

export function findCustomerDuplicateCandidates(params: {
  incoming: {
    companyName: string;
    contactName: string;
    phone: string | null;
    email: string | null;
    ownerId: string | null;
  };
  existing: ExistingCustomerLite[];
}): Array<{ id: string; score: number; reason: string }> {
  const incomingCompany = normalize(params.incoming.companyName);
  const incomingContact = normalize(params.incoming.contactName);
  const incomingPhone = normalize(params.incoming.phone);
  const incomingEmail = normalize(params.incoming.email);

  const candidates: Array<{ id: string; score: number; reason: string }> = [];

  for (const row of params.existing) {
    let score = 0;
    const reasons: string[] = [];

    const companyScore = similarity(incomingCompany, normalize(row.companyName));
    if (companyScore > 0) {
      score += companyScore * 0.45;
      reasons.push("company");
    }

    if (incomingEmail && incomingEmail === normalize(row.email)) {
      score += 0.35;
      reasons.push("email");
    }

    if (incomingPhone && incomingPhone === normalize(row.phone)) {
      score += 0.25;
      reasons.push("phone");
    }

    const contactScore = similarity(incomingContact, normalize(row.contactName));
    if (contactScore > 0) {
      score += contactScore * 0.2;
      reasons.push("contact");
    }

    if (params.incoming.ownerId && params.incoming.ownerId === row.ownerId) {
      score += 0.05;
      reasons.push("owner");
    }

    if (score >= 0.55) {
      candidates.push({
        id: row.id,
        score: Number(Math.min(score, 1).toFixed(3)),
        reason: reasons.join("+")
      });
    }
  }

  return candidates.sort((a, b) => b.score - a.score);
}

export function findOpportunityDuplicateCandidates(params: {
  incoming: {
    customerId: string | null;
    title: string;
    amount: number | null;
    stage: string | null;
    ownerId: string | null;
  };
  existing: ExistingOpportunityLite[];
}): Array<{ id: string; score: number; reason: string }> {
  const title = normalize(params.incoming.title);
  const candidates: Array<{ id: string; score: number; reason: string }> = [];

  for (const row of params.existing) {
    let score = 0;
    const reasons: string[] = [];

    if (params.incoming.customerId && row.customerId === params.incoming.customerId) {
      score += 0.35;
      reasons.push("customer");
    }

    const titleScore = similarity(title, normalize(row.title));
    if (titleScore > 0) {
      score += titleScore * 0.35;
      reasons.push("title");
    }

    if (params.incoming.amount !== null) {
      const delta = Math.abs(row.amount - params.incoming.amount);
      if (delta <= 1000) {
        score += 0.2;
        reasons.push("amount");
      } else if (delta <= 5000) {
        score += 0.12;
        reasons.push("amount_near");
      }
    }

    if (params.incoming.stage && params.incoming.stage === row.stage) {
      score += 0.08;
      reasons.push("stage");
    }
    if (params.incoming.ownerId && params.incoming.ownerId === row.ownerId) {
      score += 0.05;
      reasons.push("owner");
    }

    if (score >= 0.55) {
      candidates.push({
        id: row.id,
        score: Number(Math.min(score, 1).toFixed(3)),
        reason: reasons.join("+")
      });
    }
  }

  return candidates.sort((a, b) => b.score - a.score);
}

export function mergeCustomerPayload(params: {
  existing: {
    name: string;
    company_name: string;
    contact_name: string;
    phone: string | null;
    email: string | null;
    source_channel: string | null;
    current_stage: string;
    next_followup_at: string | null;
    risk_level: string;
    tags: string[];
    ai_summary: string | null;
  };
  incoming: Partial<{
    name: string;
    company_name: string;
    contact_name: string;
    phone: string | null;
    email: string | null;
    source_channel: string | null;
    current_stage: string;
    next_followup_at: string | null;
    risk_level: string;
    tags: string[];
    ai_summary: string | null;
  }>;
}): Partial<{
  name: string;
  company_name: string;
  contact_name: string;
  phone: string | null;
  email: string | null;
  source_channel: string | null;
  current_stage: string;
  next_followup_at: string | null;
  risk_level: string;
  tags: string[];
  ai_summary: string | null;
}> {
  const next = { ...params.existing };
  const fields: Array<keyof typeof next> = [
    "name",
    "company_name",
    "contact_name",
    "phone",
    "email",
    "source_channel",
    "next_followup_at",
    "ai_summary"
  ];

  for (const key of fields) {
    const incoming = params.incoming[key];
    const current = next[key];
    if ((current === null || current === "" || current === undefined) && incoming !== undefined && incoming !== null && incoming !== "") {
      (next as any)[key] = incoming;
    }
  }

  if (params.incoming.tags && params.incoming.tags.length > 0) {
    next.tags = Array.from(new Set([...(params.existing.tags ?? []), ...params.incoming.tags]));
  }

  if (!params.existing.current_stage && params.incoming.current_stage) {
    next.current_stage = params.incoming.current_stage;
  }
  if (!params.existing.risk_level && params.incoming.risk_level) {
    next.risk_level = params.incoming.risk_level;
  }

  return next;
}

