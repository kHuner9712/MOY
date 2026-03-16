export type InboundLeadSource =
  | "website_demo"
  | "website_trial"
  | "website_contact"
  | "referral"
  | "manual"
  | "event"
  | "content_download";

export type InboundLeadStatus =
  | "new"
  | "qualified"
  | "unqualified"
  | "demo_scheduled"
  | "trial_started"
  | "converted_to_customer"
  | "lost";

export type DemoRequestStatus = "pending" | "scheduled" | "completed" | "no_show" | "cancelled";
export type DemoOutcomeStatus = "promising" | "neutral" | "not_fit" | "followup_needed";
export type TrialRequestStatus = "pending" | "approved" | "rejected" | "activated" | "expired";

export type TrialConversionStage =
  | "invited"
  | "activated"
  | "onboarding_started"
  | "onboarding_completed"
  | "first_value_seen"
  | "active_trial"
  | "conversion_discussion"
  | "verbally_committed"
  | "converted"
  | "churned";

export type ConversionEventType =
  | "lead_created"
  | "demo_requested"
  | "demo_scheduled"
  | "demo_completed"
  | "trial_requested"
  | "trial_approved"
  | "trial_activated"
  | "onboarding_completed"
  | "first_deal_created"
  | "first_brief_generated"
  | "conversion_signal"
  | "converted"
  | "churn_risk";

export type MarketingPageKey = "home" | "product" | "industries" | "demo" | "trial" | "contact";
export type MarketingPageStatus = "draft" | "published";

export interface InboundLead {
  id: string;
  orgId: string;
  leadSource: InboundLeadSource;
  companyName: string;
  contactName: string;
  email: string;
  phone: string | null;
  industryHint: string | null;
  teamSizeHint: string | null;
  useCaseHint: string | null;
  sourceCampaign: string | null;
  landingPage: string | null;
  status: InboundLeadStatus;
  assignedOwnerId: string | null;
  assignedOwnerName?: string;
  convertedCustomerId: string | null;
  convertedOpportunityId: string | null;
  notes: string | null;
  payloadSnapshot: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export interface DemoRequest {
  id: string;
  orgId: string;
  leadId: string;
  requestedByEmail: string;
  requestedAt: string;
  preferredTimeText: string | null;
  demoStatus: DemoRequestStatus;
  scheduledEventId: string | null;
  ownerId: string | null;
  ownerName?: string;
  demoSummary: string | null;
  outcomeStatus: DemoOutcomeStatus | null;
  createdAt: string;
  updatedAt: string;
}

export interface TrialRequest {
  id: string;
  orgId: string;
  leadId: string;
  requestedByEmail: string;
  requestedAt: string;
  requestedTemplateId: string | null;
  requestStatus: TrialRequestStatus;
  targetOrgId: string | null;
  activationToken: string | null;
  activationStartedAt: string | null;
  activatedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface TrialConversionTrack {
  id: string;
  orgId: string;
  targetOrgId: string;
  leadId: string | null;
  ownerId: string;
  ownerName?: string;
  currentStage: TrialConversionStage;
  activationScore: number;
  engagementScore: number;
  conversionReadinessScore: number;
  riskFlags: string[];
  nextAction: string | null;
  nextActionDueAt: string | null;
  summary: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ConversionEvent {
  id: string;
  orgId: string;
  targetOrgId: string | null;
  leadId: string | null;
  eventType: ConversionEventType;
  eventSummary: string;
  eventPayload: Record<string, unknown>;
  createdAt: string;
}

export interface MarketingPage {
  id: string;
  pageKey: MarketingPageKey;
  status: MarketingPageStatus;
  title: string;
  subtitle: string;
  contentPayload: Record<string, unknown>;
  seoPayload: Record<string, unknown>;
  updatedBy: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface LeadAssignmentRule {
  id: string;
  orgId: string;
  ruleName: string;
  sourceFilter: string[];
  industryFilter: string[];
  teamSizeFilter: string[];
  assignToUserId: string;
  assignToUserName?: string;
  priority: number;
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface LeadQualificationAssistResult {
  qualificationAssessment: string;
  fitScore: number;
  likelyUseCase: string;
  suggestedOwnerType: "sales" | "manager";
  suggestedNextActions: string[];
  riskFlags: string[];
}

export interface TrialConversionReviewResult {
  activationHealth: string;
  readinessAssessment: string;
  riskFactors: string[];
  recommendedConversionActions: string[];
  recommendedOwnerFollowup: string[];
}

export interface GrowthPipelineSummaryResult {
  funnelSummary: string;
  bestChannels: string[];
  weakPoints: string[];
  highPotentialSegments: string[];
  nextBestActions: string[];
}

export interface GrowthSummary {
  periodDays: number;
  leadsTotal: number;
  leadsNew: number;
  leadsQualified: number;
  demoRequested: number;
  demoCompleted: number;
  demoCompletionRate: number;
  trialRequested: number;
  trialActivated: number;
  trialActivationRate: number;
  onboardingCompletedCount: number;
  onboardingCompletionRate: number;
  conversionReadyCount: number;
  convertedCount: number;
  bySource: Array<{ source: InboundLeadSource; count: number }>;
  byIndustry: Array<{ industry: string; leadCount: number; trialCount: number; convertedCount: number }>;
  highRiskTracks: TrialConversionTrack[];
  recentEvents: ConversionEvent[];
  aiSummary: GrowthPipelineSummaryResult;
  aiSummaryUsedFallback: boolean;
  aiSummaryFallbackReason: string | null;
}
