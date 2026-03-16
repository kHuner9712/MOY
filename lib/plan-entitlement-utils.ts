import type { EntitlementStatus } from "@/types/productization";

export function canRunAiByEntitlement(status: EntitlementStatus): { allowed: boolean; reason: string | null } {
  if (status.status !== "active") {
    return {
      allowed: false,
      reason: "Organization plan is not active"
    };
  }

  if (status.aiRunUsedMonthly >= status.aiRunLimitMonthly) {
    return {
      allowed: false,
      reason: "Monthly AI quota reached"
    };
  }

  return {
    allowed: true,
    reason: null
  };
}

export function canProcessDocumentsByEntitlement(status: EntitlementStatus): { allowed: boolean; reason: string | null } {
  if (status.documentUsedMonthly >= status.documentLimitMonthly) {
    return {
      allowed: false,
      reason: "Monthly document quota reached"
    };
  }

  return {
    allowed: true,
    reason: null
  };
}

export function canUseTouchpointsByEntitlement(status: EntitlementStatus): { allowed: boolean; reason: string | null } {
  if (status.touchpointUsedMonthly >= status.touchpointLimitMonthly) {
    return {
      allowed: false,
      reason: "Monthly touchpoint quota reached"
    };
  }

  return {
    allowed: true,
    reason: null
  };
}

export function hasSeatCapacity(status: EntitlementStatus): { allowed: boolean; reason: string | null } {
  if (status.seatUsed >= status.seatLimit) {
    return {
      allowed: false,
      reason: "Seat limit reached"
    };
  }

  return {
    allowed: true,
    reason: null
  };
}
