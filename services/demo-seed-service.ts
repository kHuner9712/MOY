import { randomUUID } from "crypto";

import { summarizeDemoSeedSteps } from "@/lib/demo-seed-summary";
import type { ServerSupabaseClient } from "@/lib/supabase/types";

interface SeedStepResult {
  name: string;
  success: boolean;
  inserted: number;
  message: string;
}

export interface DemoSeedRunResult {
  runId: string;
  status: "completed" | "failed";
  partialSuccess: boolean;
  summary: string;
  steps: SeedStepResult[];
  inserted: Record<string, number>;
}

type DbClient = ServerSupabaseClient;

type ProfileLite = {
  id: string;
  role: "sales" | "manager";
};

function nowIso(): string {
  return new Date().toISOString();
}

function daysAfter(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return date.toISOString();
}

async function appendOnboardingRun(params: {
  supabase: DbClient;
  runId: string;
  status: "running" | "completed" | "failed";
  summary?: string;
  detailSnapshot?: Record<string, unknown>;
}): Promise<void> {
  const payload: Record<string, unknown> = {
    status: params.status
  };
  if (params.summary !== undefined) payload.summary = params.summary;
  if (params.detailSnapshot !== undefined) payload.detail_snapshot = params.detailSnapshot;

  const res = await params.supabase.from("onboarding_runs").update(payload).eq("id", params.runId);
  if (res.error) throw new Error(res.error.message);
}

export async function runDemoSeed(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  runType?: "demo_seed" | "reinitialize_demo";
  templateKey?: string;
}): Promise<DemoSeedRunResult> {
  const runType = params.runType ?? "demo_seed";

  const templateProfiles: Record<string, { companyPrefix: string; sourceA: string; sourceB: string; stageHintA: string; stageHintB: string; stageHintC: string }> = {
    generic: {
      companyPrefix: "Demo Customer",
      sourceA: "官网线索",
      sourceB: "渠道推荐",
      stageHintA: "proposal",
      stageHintB: "needs_confirmed",
      stageHintC: "negotiation"
    },
    b2b_software: {
      companyPrefix: "SaaS 客户",
      sourceA: "产品试用",
      sourceB: "生态合作",
      stageHintA: "proposal",
      stageHintB: "needs_confirmed",
      stageHintC: "negotiation"
    },
    education_training: {
      companyPrefix: "培训机构",
      sourceA: "试听转化",
      sourceB: "地推获客",
      stageHintA: "needs_confirmed",
      stageHintB: "proposal",
      stageHintC: "negotiation"
    },
    manufacturing: {
      companyPrefix: "制造客户",
      sourceA: "技术会展",
      sourceB: "老客转介绍",
      stageHintA: "proposal",
      stageHintB: "needs_confirmed",
      stageHintC: "negotiation"
    },
    channel_sales: {
      companyPrefix: "渠道伙伴",
      sourceA: "渠道招募会",
      sourceB: "区域合作",
      stageHintA: "initial_contact",
      stageHintB: "needs_confirmed",
      stageHintC: "proposal"
    },
    consulting_services: {
      companyPrefix: "咨询客户",
      sourceA: "诊断会议",
      sourceB: "管理层引荐",
      stageHintA: "needs_confirmed",
      stageHintB: "proposal",
      stageHintC: "negotiation"
    }
  };
  const templateProfile = templateProfiles[params.templateKey ?? "generic"] ?? templateProfiles.generic;

  const runRes = await params.supabase
    .from("onboarding_runs")
    .insert({
      org_id: params.orgId,
      initiated_by: params.actorUserId,
      run_type: runType,
      status: "running",
      summary: "Demo seed started",
      detail_snapshot: {}
    })
    .select("id")
    .single();

  if (runRes.error || !runRes.data) throw new Error(runRes.error?.message ?? "failed_to_create_onboarding_run");
  const runId = (runRes.data as { id: string }).id;

  const steps: SeedStepResult[] = [];
  const inserted: Record<string, number> = {
    customers: 0,
    followups: 0,
    opportunities: 0,
    alerts: 0,
    work_items: 0,
    deal_rooms: 0,
    prep_cards: 0,
    playbooks: 0,
    touchpoints: 0
  };

  try {
    const profileRes = await params.supabase
      .from("profiles")
      .select("id,role")
      .eq("org_id", params.orgId)
      .eq("is_active", true)
      .order("created_at", { ascending: true });
    if (profileRes.error) throw new Error(profileRes.error.message);

    const profiles = (profileRes.data ?? []) as ProfileLite[];
    if (profiles.length === 0) throw new Error("No active profiles found for demo seed");

    const manager = profiles.find((item) => item.role === "manager") ?? profiles[0];
    const sales = profiles.filter((item) => item.role === "sales");
    const salesPool = sales.length > 0 ? sales : [profiles[0]];

    const customerRows = Array.from({ length: 6 }).map((_, index) => {
      const owner = salesPool[index % salesPool.length];
      const id = randomUUID();
      return {
        id,
        org_id: params.orgId,
        owner_id: owner.id,
        name: `Demo Contact ${index + 1}`,
        company_name: `${templateProfile.companyPrefix} ${index + 1}`,
        contact_name: `Contact ${index + 1}`,
        phone: `1380000${(100 + index).toString()}`,
        email: `demo${index + 1}@example.com`,
        source_channel: index % 2 === 0 ? templateProfile.sourceA : templateProfile.sourceB,
        current_stage: index % 3 === 0 ? templateProfile.stageHintA : index % 3 === 1 ? templateProfile.stageHintB : templateProfile.stageHintC,
        last_followup_at: daysAfter(-(index + 1)),
        next_followup_at: daysAfter(index % 2 === 0 ? 1 : 2),
        win_probability: 40 + index * 8,
        risk_level: index % 3 === 0 ? "high" : "medium",
        tags: ["demo", index % 2 === 0 ? "high-value" : "standard"],
        ai_summary: "Demo seeded customer summary",
        ai_suggestion: "Prioritize followup and decision maker confirmation",
        ai_risk_judgement: "Demo seed risk judgement",
        has_decision_maker: index % 2 === 0,
        created_by: owner.id
      };
    });

    const customerInsert = await params.supabase.from("customers").insert(customerRows).select("id,owner_id");
    if (customerInsert.error) {
      steps.push({ name: "customers", success: false, inserted: 0, message: customerInsert.error.message });
    } else {
      const customerData = (customerInsert.data ?? []) as Array<{ id: string; owner_id: string }>;
      inserted.customers = customerData.length;
      steps.push({ name: "customers", success: true, inserted: customerData.length, message: "ok" });

      const followupRows = customerData.map((customer, index) => ({
        org_id: params.orgId,
        customer_id: customer.id,
        owner_id: customer.owner_id,
        communication_type: index % 2 === 0 ? "phone" : "wechat",
        summary: "Demo seeded followup summary",
        customer_needs: "Need phased rollout and onboarding support",
        objections: index % 2 === 0 ? "Budget sensitivity" : "Timeline uncertainty",
        next_step: "Schedule next stakeholder alignment",
        next_followup_at: daysAfter(2),
        needs_ai_analysis: true,
        created_by: customer.owner_id
      }));

      const followupInsert = await params.supabase.from("followups").insert(followupRows).select("id,customer_id,owner_id");
      if (followupInsert.error) {
        steps.push({ name: "followups", success: false, inserted: 0, message: followupInsert.error.message });
      } else {
        const followupData = (followupInsert.data ?? []) as Array<{ id: string; customer_id: string; owner_id: string }>;
        inserted.followups = followupData.length;
        steps.push({ name: "followups", success: true, inserted: followupData.length, message: "ok" });

        const opportunityRows = customerData.slice(0, 4).map((customer, index) => ({
          org_id: params.orgId,
          customer_id: customer.id,
          owner_id: customer.owner_id,
          title: `Demo Opportunity ${index + 1}`,
          amount: 30000 + index * 12000,
          stage: index % 2 === 0 ? "proposal" : "negotiation",
          risk_level: index % 2 === 0 ? "medium" : "high",
          expected_close_date: daysAfter(18 + index).slice(0, 10),
          last_activity_at: nowIso(),
          created_by: customer.owner_id
        }));

        const oppInsert = await params.supabase.from("opportunities").insert(opportunityRows).select("id,customer_id,owner_id");
        if (oppInsert.error) {
          steps.push({ name: "opportunities", success: false, inserted: 0, message: oppInsert.error.message });
        } else {
          const oppData = (oppInsert.data ?? []) as Array<{ id: string; customer_id: string; owner_id: string }>;
          inserted.opportunities = oppData.length;
          steps.push({ name: "opportunities", success: true, inserted: oppData.length, message: "ok" });

          const alertRows = oppData.slice(0, 3).map((item, index) => ({
            org_id: params.orgId,
            customer_id: item.customer_id,
            opportunity_id: item.id,
            owner_id: item.owner_id,
            rule_type: index % 2 === 0 ? "quoted_but_stalled" : "no_followup_timeout",
            severity: index % 2 === 0 ? "warning" : "critical",
            status: "open",
            source: "rule",
            title: "Demo alert",
            description: "Seeded alert for onboarding demo",
            evidence: ["seed-demo"],
            suggested_owner_action: ["Follow up within 24h"],
            due_at: daysAfter(1)
          }));

          const alertInsert = await params.supabase.from("alerts").insert(alertRows).select("id");
          if (alertInsert.error) {
            steps.push({ name: "alerts", success: false, inserted: 0, message: alertInsert.error.message });
          } else {
            inserted.alerts = (alertInsert.data ?? []).length;
            steps.push({ name: "alerts", success: true, inserted: inserted.alerts, message: "ok" });
          }

          const workItemRows = customerData.slice(0, 5).map((customer, index) => ({
            org_id: params.orgId,
            owner_id: customer.owner_id,
            customer_id: customer.id,
            source_type: "ai_suggested",
            work_type: index % 2 === 0 ? "followup_call" : "prepare_proposal",
            title: `Demo task ${index + 1}`,
            description: "Seeded task for onboarding demo",
            rationale: "Demo seed",
            priority_score: 60 + index * 5,
            priority_band: index < 2 ? "high" : "medium",
            status: "todo",
            due_at: daysAfter(index + 1),
            ai_generated: true,
            created_by: customer.owner_id
          }));

          const workInsert = await params.supabase.from("work_items").insert(workItemRows).select("id,customer_id,owner_id");
          if (workInsert.error) {
            steps.push({ name: "work_items", success: false, inserted: 0, message: workInsert.error.message });
          } else {
            inserted.work_items = (workInsert.data ?? []).length;
            steps.push({ name: "work_items", success: true, inserted: inserted.work_items, message: "ok" });
          }

          const roomRows = oppData.slice(0, 2).map((opp, index) => ({
            org_id: params.orgId,
            customer_id: opp.customer_id,
            opportunity_id: opp.id,
            owner_id: opp.owner_id,
            room_status: index === 0 ? "active" : "watchlist",
            priority_band: index === 0 ? "strategic" : "important",
            title: `Demo Deal Room ${index + 1}`,
            command_summary: "Seeded command summary",
            current_goal: "Move deal to next milestone",
            current_blockers: [],
            next_milestone: "Quote sent",
            next_milestone_due_at: daysAfter(5),
            manager_attention_needed: index === 0,
            source_snapshot: { source: "demo_seed", template_key: params.templateKey ?? "generic" },
            created_by: manager.id
          }));

          const roomInsert = await params.supabase.from("deal_rooms").insert(roomRows).select("id,owner_id");
          if (roomInsert.error) {
            steps.push({ name: "deal_rooms", success: false, inserted: 0, message: roomInsert.error.message });
          } else {
            const roomData = (roomInsert.data ?? []) as Array<{ id: string; owner_id: string }>;
            inserted.deal_rooms = roomData.length;
            steps.push({ name: "deal_rooms", success: true, inserted: roomData.length, message: "ok" });

            const participantRows = roomData.map((room) => ({
              org_id: params.orgId,
              deal_room_id: room.id,
              user_id: room.owner_id,
              role_in_room: "owner",
              is_active: true
            }));
            await params.supabase.from("deal_participants").insert(participantRows);
          }

          const prepRows = customerData.slice(0, 3).map((customer, index) => ({
            org_id: params.orgId,
            owner_id: customer.owner_id,
            customer_id: customer.id,
            card_type: index % 2 === 0 ? "followup_prep" : "task_brief",
            status: "ready",
            title: `Demo prep ${index + 1}`,
            summary: "Seeded preparation card",
            card_payload: {
              current_state_summary: "Seeded payload",
              key_points_to_mention: ["business impact", "timeline"],
              recommended_angle: "value-first"
            },
            source_snapshot: { source: "demo_seed", template_key: params.templateKey ?? "generic" },
            generated_by: manager.id,
            valid_until: daysAfter(2)
          }));

          const prepInsert = await params.supabase.from("prep_cards").insert(prepRows).select("id");
          if (prepInsert.error) {
            steps.push({ name: "prep_cards", success: false, inserted: 0, message: prepInsert.error.message });
          } else {
            inserted.prep_cards = (prepInsert.data ?? []).length;
            steps.push({ name: "prep_cards", success: true, inserted: inserted.prep_cards, message: "ok" });
          }

          const playbookInsert = await params.supabase
            .from("playbooks")
            .insert({
              org_id: params.orgId,
              scope_type: "team",
              owner_user_id: null,
              playbook_type: "followup_rhythm",
              title: "Demo Followup Rhythm Playbook",
              summary: "Follow up in 48 hours with concrete next step improves progression.",
              status: "active",
              confidence_score: 0.72,
              applicability_notes: "Best for proposal/negotiation stage",
              source_snapshot: { source: "demo_seed" },
              generated_by: manager.id
            })
            .select("id")
            .maybeSingle();

          if (playbookInsert.error || !playbookInsert.data) {
            steps.push({ name: "playbooks", success: false, inserted: 0, message: playbookInsert.error?.message ?? "insert failed" });
          } else {
            const playbookId = (playbookInsert.data as { id: string }).id;
            const entryInsert = await params.supabase.from("playbook_entries").insert({
              org_id: params.orgId,
              playbook_id: playbookId,
              entry_title: "48h followup rhythm",
              entry_summary: "Send business-value recap and lock next owner.",
              conditions: { stage: ["proposal", "negotiation"] },
              recommended_actions: ["Confirm decision owner", "Set fixed next milestone"],
              caution_notes: ["Avoid over-promising timeline"],
              evidence_snapshot: { source: "demo_seed" },
              success_signal: { stage_progression: true },
              failure_modes: ["No clear next step"],
              confidence_score: 0.71,
              sort_order: 1
            });

            if (entryInsert.error) {
              steps.push({ name: "playbooks", success: false, inserted: 0, message: entryInsert.error.message });
            } else {
              inserted.playbooks = 1;
              steps.push({ name: "playbooks", success: true, inserted: 1, message: "ok" });
            }
          }

          const accountInsert = await params.supabase
            .from("external_accounts")
            .insert({
              org_id: params.orgId,
              user_id: manager.id,
              provider_type: "email",
              provider_name: "manual_upload",
              account_label: "Demo Mailbox",
              connection_status: "connected",
              metadata: { source: "demo_seed" }
            })
            .select("id")
            .maybeSingle();

          let externalAccountId: string | null = null;
          if (accountInsert.error || !accountInsert.data) {
            steps.push({ name: "touchpoints", success: false, inserted: 0, message: accountInsert.error?.message ?? "external account failed" });
          } else {
            externalAccountId = (accountInsert.data as { id: string }).id;
          }

          if (externalAccountId && customerData.length > 0) {
            const emailThreadInsert = await params.supabase
              .from("email_threads")
              .insert({
                org_id: params.orgId,
                owner_id: customerData[0].owner_id,
                customer_id: customerData[0].id,
                external_account_id: externalAccountId,
                subject: "Demo follow-up email thread",
                participants: ["sales@demo.moy", "client@example.com"],
                latest_message_at: nowIso(),
                thread_status: "waiting_reply",
                sentiment_hint: "neutral",
                summary: "Seeded touchpoint thread",
                source_snapshot: { source: "demo_seed" }
              })
              .select("id")
              .maybeSingle();

            if (emailThreadInsert.error || !emailThreadInsert.data) {
              steps.push({ name: "touchpoints", success: false, inserted: 0, message: emailThreadInsert.error?.message ?? "email thread failed" });
            } else {
              const threadId = (emailThreadInsert.data as { id: string }).id;
              await params.supabase.from("email_messages").insert({
                org_id: params.orgId,
                thread_id: threadId,
                sender_user_id: customerData[0].owner_id,
                direction: "outbound",
                message_subject: "Demo quote follow-up",
                message_body_text: "Can we align on next review meeting this week?",
                message_body_markdown: "Can we align on **next review meeting** this week?",
                sent_at: nowIso(),
                status: "sent",
                source_type: "manual"
              });

              await params.supabase.from("calendar_events").insert({
                org_id: params.orgId,
                owner_id: customerData[0].owner_id,
                customer_id: customerData[0].id,
                event_type: "customer_meeting",
                title: "Demo customer review meeting",
                description: "Seeded calendar event",
                attendees: ["sales@demo.moy", "client@example.com"],
                start_at: daysAfter(1),
                end_at: daysAfter(1),
                meeting_status: "scheduled",
                source_snapshot: { source: "demo_seed", template_key: params.templateKey ?? "generic" }
              });

              await params.supabase.from("document_assets").insert({
                org_id: params.orgId,
                owner_id: customerData[0].owner_id,
                customer_id: customerData[0].id,
                source_type: "generated",
                document_type: "proposal",
                title: "Demo proposal",
                file_name: "demo-proposal.md",
                mime_type: "text/markdown",
                extracted_text: "Seed proposal text",
                summary: "Seeded proposal summary",
                tags: ["demo", "proposal"]
              });

              await params.supabase.from("external_touchpoint_events").insert({
                org_id: params.orgId,
                owner_id: customerData[0].owner_id,
                customer_id: customerData[0].id,
                touchpoint_type: "email",
                event_type: "email_sent",
                event_summary: "Seeded outbound email touchpoint",
                event_payload: { source: "demo_seed", thread_id: threadId, template_key: params.templateKey ?? "generic" }
              });

              inserted.touchpoints = 4;
              steps.push({ name: "touchpoints", success: true, inserted: inserted.touchpoints, message: "ok" });
            }
          }
        }
      }
    }

    const summaryResult = summarizeDemoSeedSteps(steps);
    const partialSuccess = summaryResult.partialSuccess;
    const status = summaryResult.status;
    const summary = summaryResult.summary;

    await appendOnboardingRun({
      supabase: params.supabase,
      runId,
      status,
      summary,
      detailSnapshot: {
        inserted,
        steps,
        partial_success: partialSuccess
      }
    });

    return {
      runId,
      status,
      partialSuccess,
      summary,
      steps,
      inserted
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "demo_seed_failed";
    await appendOnboardingRun({
      supabase: params.supabase,
      runId,
      status: "failed",
      summary: message,
      detailSnapshot: {
        inserted,
        steps,
        fatal_error: message
      }
    }).catch(() => null);

    return {
      runId,
      status: "failed",
      partialSuccess: true,
      summary: message,
      steps,
      inserted
    };
  }
}

