import { createSupabaseServerClient } from "@/lib/supabase/server";
import {
  listNotifications,
  getNotificationById,
  getNotificationStats,
  getUserNotificationPreferences,
  upsertNotificationPreference,
  sendPendingNotifications
} from "@/services/notification-service";
import { NextResponse } from "next/server";

export async function GET(request: Request) {
  try {
    const supabase = createSupabaseServerClient();
    if (!supabase) {
      return NextResponse.json({ success: false, error: "Supabase not configured" }, { status: 500 });
    }

    const {
      data: { user }
    } = await supabase.auth.getUser();

    if (!user) {
      return NextResponse.json({ success: false, error: "Unauthorized" }, { status: 401 });
    }

    const { data: profile } = await supabase
      .from("profiles")
      .select("id, org_id, role")
      .eq("id", user.id)
      .maybeSingle();

    type ProfileRow = { id: string; org_id: string | null; role: string };
    const typedProfile = profile as ProfileRow | null;

    if (!typedProfile?.org_id) {
      return NextResponse.json({ success: false, error: "No organization found" }, { status: 400 });
    }

    const orgId = typedProfile.org_id;

    const url = new URL(request.url);
    const action = url.searchParams.get("action") ?? "list";

    if (action === "stats") {
      const stats = await getNotificationStats({
        supabase,
        orgId,
        userId: user.id
      });

      return NextResponse.json({
        success: true,
        data: stats
      });
    }

    if (action === "preferences") {
      const preferences = await getUserNotificationPreferences({
        supabase,
        userId: user.id
      });

      return NextResponse.json({
        success: true,
        data: preferences
      });
    }

    if (action === "send") {
      const result = await sendPendingNotifications({
        supabase,
        orgId
      });

      return NextResponse.json({
        success: true,
        data: result
      });
    }

    const status = url.searchParams.getAll("status") ?? undefined;
    const priority = url.searchParams.getAll("priority") ?? undefined;
    const sourceType = url.searchParams.getAll("sourceType") ?? undefined;
    const channel = url.searchParams.getAll("channel") ?? undefined;
    const limit = url.searchParams.get("limit") ? parseInt(url.searchParams.get("limit") as string) : 50;
    const offset = url.searchParams.get("offset") ? parseInt(url.searchParams.get("offset") as string) : 0;

    const result = await listNotifications({
      supabase,
      orgId,
      userId: user.id,
      status: status as any,
      priority: priority as any,
      sourceType: sourceType as any,
      channel: channel as any,
      limit,
      offset
    });

    return NextResponse.json({
      success: true,
      data: result.notifications,
      meta: {
        total: result.total,
        limit,
        offset
      }
    });
  } catch (error) {
    console.error("[api/notifications] Error:", error);
    return NextResponse.json(
      {
        success: false,
        error: error instanceof Error ? error.message : "Failed to get notifications"
      },
      { status: 500 }
    );
  }
}

export async function POST(request: Request) {
  try {
    const supabase = createSupabaseServerClient();
    if (!supabase) {
      return NextResponse.json({ success: false, error: "Supabase not configured" }, { status: 500 });
    }

    const {
      data: { user }
    } = await supabase.auth.getUser();

    if (!user) {
      return NextResponse.json({ success: false, error: "Unauthorized" }, { status: 401 });
    }

    const { data: profile } = await supabase
      .from("profiles")
      .select("id, org_id, role")
      .eq("id", user.id)
      .maybeSingle();

    type ProfileRow = { id: string; org_id: string | null; role: string };
    const typedProfilePost = profile as ProfileRow | null;

    if (!typedProfilePost?.org_id) {
      return NextResponse.json({ success: false, error: "No organization found" }, { status: 400 });
    }

    const orgIdPost = typedProfilePost.org_id;

    const body = await request.json();
    const { action } = body;

    if (action === "update_preference") {
      const { channel, isEnabled, config } = body;

      const preference = await upsertNotificationPreference({
        supabase,
        orgId: orgIdPost,
        userId: user.id,
        channel,
        isEnabled,
        config
      });

      return NextResponse.json({
        success: true,
        data: preference
      });
    }

    return NextResponse.json({ success: false, error: "Invalid action" }, { status: 400 });
  } catch (error) {
    console.error("[api/notifications] Error:", error);
    return NextResponse.json(
      {
        success: false,
        error: error instanceof Error ? error.message : "Failed to process notification request"
      },
      { status: 500 }
    );
  }
}
