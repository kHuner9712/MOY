/**
 * v1.2 Manager Desk API
 * 服务端专用路由
 */

import { NextRequest, NextResponse } from "next/server";
import { getServerAuthContext } from "@/lib/server-auth";
import { managerDeskServerService } from "@/services/manager-desk-server-service";
import { fail, ok } from "@/lib/api-response";
import type { ManagerIntervention } from "@/types/manager-desk";

export async function GET(request: NextRequest) {
  try {
    const auth = await getServerAuthContext();
    if (!auth) return fail("Unauthorized", 401);

    const result = await managerDeskServerService.getManagerDesk({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      ownerId: auth.profile.id
    });

    return NextResponse.json(result);
  } catch (error) {
    console.error("Manager desk error:", error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Internal server error" },
      { status: 500 }
    );
  }
}

export async function POST(request: NextRequest) {
  try {
    const auth = await getServerAuthContext();
    if (!auth) return fail("Unauthorized", 401);
    if (!auth.profile.role?.includes("manager")) {
      return fail("Manager role required", 403);
    }

    const body = await request.json();
    const intervention = body.intervention as ManagerIntervention | undefined;
    if (!intervention) return fail("intervention is required", 400);

    const result = await managerDeskServerService.createInterventionWorkItem({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      intervention,
      actorUserId: auth.profile.id
    });

    return ok(result);
  } catch (error) {
    console.error("Manager desk POST error:", error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Internal server error" },
      { status: 500 }
    );
  }
}

export async function PATCH(request: NextRequest) {
  try {
    const auth = await getServerAuthContext();
    if (!auth) return fail("Unauthorized", 401);
    if (!auth.profile.role?.includes("manager")) {
      return fail("Manager role required", 403);
    }

    const body = await request.json();
    const { interventionKey, resolution, outcomeNote, intervention } = body as {
      interventionKey: string;
      resolution: "completed" | "dismissed";
      outcomeNote?: string;
      intervention: ManagerIntervention;
    };

    if (!interventionKey || !resolution) {
      return fail("interventionKey and resolution are required", 400);
    }
    if (resolution !== "completed" && resolution !== "dismissed") {
      return fail("resolution must be completed or dismissed", 400);
    }

    const result = await managerDeskServerService.resolveIntervention({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      interventionKey,
      resolution,
      actorUserId: auth.profile.id,
      outcomeNote,
      intervention
    });

    return ok(result);
  } catch (error) {
    console.error("Manager desk PATCH error:", error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Internal server error" },
      { status: 500 }
    );
  }
}
