export interface CommercialReadinessGateSpec {
  id: string;
  key: string;
  title: string;
}

export const COMMERCIAL_READINESS_GATE_SPECS: CommercialReadinessGateSpec[] = [
  { id: "CR-01", key: "domain_clarity", title: "Domain Clarity" },
  { id: "CR-02", key: "permission_consistency", title: "Permission Consistency" },
  { id: "CR-03", key: "source_traceability", title: "Source Traceability" },
  { id: "CR-04", key: "event_action_linkage", title: "Event and Action Linkage" },
  { id: "CR-05", key: "ai_fallback_behavior", title: "AI Fallback Behavior" },
  { id: "CR-06", key: "governance_audit_expectations", title: "Governance and Audit Expectations" },
  { id: "CR-07", key: "public_commercial_entry_integrity", title: "Public Commercial Entry Integrity" },
  { id: "CR-08", key: "test_coverage_expectations", title: "Test Coverage Expectations" },
  { id: "CR-09", key: "build_release_quality_gates", title: "Build and Release Quality Gates" }
];

export interface CommercialReadinessChecklistValidation {
  missingGateIds: string[];
  missingGateKeys: string[];
  missingRequiredSections: string[];
}

function includesIgnoreCase(text: string, fragment: string): boolean {
  return text.toLowerCase().includes(fragment.toLowerCase());
}

export function validateCommercialReadinessChecklist(markdown: string): CommercialReadinessChecklistValidation {
  const missingGateIds: string[] = [];
  const missingGateKeys: string[] = [];

  for (const gate of COMMERCIAL_READINESS_GATE_SPECS) {
    if (!includesIgnoreCase(markdown, gate.id)) {
      missingGateIds.push(gate.id);
    }
    if (!includesIgnoreCase(markdown, gate.key)) {
      missingGateKeys.push(gate.key);
    }
  }

  const requiredSections = [
    "## Gate Scorecard",
    "## Release Decision Record",
    "## Validation Commands"
  ];
  const missingRequiredSections = requiredSections.filter((section) => !includesIgnoreCase(markdown, section));

  return {
    missingGateIds,
    missingGateKeys,
    missingRequiredSections
  };
}
