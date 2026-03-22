import assert from "node:assert/strict";

import { canAccessPath, canAccessPathForUser } from "../lib/auth";
import {
  canAccessExecutive,
  canManageAutomationRules,
  canManageOrgCustomization,
  canManageTemplates,
  canTriggerAutomationRules,
  canViewAutomationCenter,
  canViewManagerWorkspace,
  canViewOrgUsage,
  isOrgAdminLike,
  resolveEffectiveOrgRole
} from "../lib/role-capability";
import type { User } from "../types/auth";
import type { OrgMemberRole } from "../types/productization";

function buildUser(displayRole: User["role"], orgRole?: OrgMemberRole): User {
  return {
    id: "user-1",
    orgId: "org-1",
    name: "Test User",
    role: displayRole,
    title: "Sales",
    email: "test@example.com",
    team: "Team A",
    orgRole
  };
}

export function runRolePermissionModelTests(logPass: (name: string) => void): void {
  const cases: Array<{
    label: string;
    user: User;
    expect: {
      adminLike: boolean;
      managerWorkspace: boolean;
      orgUsage: boolean;
      executive: boolean;
      automationView: boolean;
      automationManage: boolean;
      automationRun: boolean;
      templateManage: boolean;
      customizationManage: boolean;
    };
  }> = [
    {
      label: "owner",
      user: buildUser("manager", "owner"),
      expect: {
        adminLike: true,
        managerWorkspace: true,
        orgUsage: true,
        executive: true,
        automationView: true,
        automationManage: true,
        automationRun: true,
        templateManage: true,
        customizationManage: true
      }
    },
    {
      label: "admin",
      user: buildUser("manager", "admin"),
      expect: {
        adminLike: true,
        managerWorkspace: true,
        orgUsage: true,
        executive: true,
        automationView: true,
        automationManage: true,
        automationRun: true,
        templateManage: true,
        customizationManage: true
      }
    },
    {
      label: "manager",
      user: buildUser("manager", "manager"),
      expect: {
        adminLike: false,
        managerWorkspace: true,
        orgUsage: true,
        executive: true,
        automationView: true,
        automationManage: false,
        automationRun: false,
        templateManage: false,
        customizationManage: false
      }
    },
    {
      label: "sales",
      user: buildUser("sales", "sales"),
      expect: {
        adminLike: false,
        managerWorkspace: false,
        orgUsage: false,
        executive: false,
        automationView: false,
        automationManage: false,
        automationRun: false,
        templateManage: false,
        customizationManage: false
      }
    },
    {
      label: "viewer",
      user: buildUser("sales", "viewer"),
      expect: {
        adminLike: false,
        managerWorkspace: false,
        orgUsage: false,
        executive: false,
        automationView: false,
        automationManage: false,
        automationRun: false,
        templateManage: false,
        customizationManage: false
      }
    }
  ];

  for (const item of cases) {
    assert.equal(isOrgAdminLike(item.user), item.expect.adminLike, `${item.label}: adminLike mismatch`);
    assert.equal(canViewManagerWorkspace(item.user), item.expect.managerWorkspace, `${item.label}: manager workspace mismatch`);
    assert.equal(canViewOrgUsage(item.user), item.expect.orgUsage, `${item.label}: org usage mismatch`);
    assert.equal(canAccessExecutive(item.user), item.expect.executive, `${item.label}: executive mismatch`);
    assert.equal(canViewAutomationCenter(item.user), item.expect.automationView, `${item.label}: automation view mismatch`);
    assert.equal(canManageAutomationRules(item.user), item.expect.automationManage, `${item.label}: automation manage mismatch`);
    assert.equal(canTriggerAutomationRules(item.user), item.expect.automationRun, `${item.label}: automation run mismatch`);
    assert.equal(canManageTemplates(item.user), item.expect.templateManage, `${item.label}: template manage mismatch`);
    assert.equal(canManageOrgCustomization(item.user), item.expect.customizationManage, `${item.label}: customization manage mismatch`);
  }
  logPass("role permission model: capability matrix");

  const legacyManager = buildUser("manager");
  const legacySales = buildUser("sales");
  assert.equal(resolveEffectiveOrgRole(legacyManager), "manager");
  assert.equal(resolveEffectiveOrgRole(legacySales), "sales");
  assert.equal(canAccessExecutive(legacyManager), true);
  assert.equal(canAccessExecutive(legacySales), false);
  logPass("role permission model: legacy display role compatibility");

  const owner = buildUser("manager", "owner");
  const manager = buildUser("manager", "manager");
  const sales = buildUser("sales", "sales");
  const viewer = buildUser("sales", "viewer");
  const ownerWithSalesDisplay = buildUser("sales", "owner");
  const salesWithManagerDisplay = buildUser("manager", "sales");

  assert.equal(canAccessPathForUser(owner, "/executive"), true);
  assert.equal(canAccessPathForUser(sales, "/executive"), false);
  assert.equal(canAccessPathForUser(manager, "/settings/templates"), true);
  assert.equal(canAccessPathForUser(sales, "/settings/templates"), false);
  assert.equal(canAccessPathForUser(ownerWithSalesDisplay, "/settings/automation"), true);
  assert.equal(canAccessPathForUser(salesWithManagerDisplay, "/settings/automation"), false);
  assert.equal(canAccessPathForUser(ownerWithSalesDisplay, "/executive"), true);
  assert.equal(canAccessPathForUser(salesWithManagerDisplay, "/executive"), false);
  assert.equal(canAccessPathForUser(manager, "/settings/usage"), true);
  assert.equal(canAccessPathForUser(viewer, "/settings/usage"), false);
  assert.equal(canAccessPathForUser(manager, "/manager"), true);
  assert.equal(canAccessPathForUser(sales, "/manager"), false);
  logPass("role permission model: high-risk path access");

  assert.equal(canAccessPath("manager", "/executive"), true);
  assert.equal(canAccessPath("sales", "/executive"), false);
  logPass("role permission model: legacy canAccessPath behavior");
}
