import type {
  DealPlaybookMappingResult,
  DecisionSupportResult,
  DealRoomCommandSummaryResult,
  InterventionRecommendationResult,
  ThreadSummaryResult
} from "@/types/ai";
import type { DealRoom } from "@/types/deal";
import type { PlaybookWithEntries } from "@/types/playbook";

function toShortList(items: string[], max = 4): string[] {
  return items.filter((item) => item.trim().length > 0).slice(0, max);
}

export function buildFallbackDealRoomCommandSummary(params: {
  room: Pick<DealRoom, "title" | "currentGoal" | "nextMilestone" | "managerAttentionNeeded">;
  blockers: string[];
  openTasks: number;
  overdueTasks: number;
  openInterventions: number;
}): DealRoomCommandSummaryResult {
  const blockers = toShortList(params.blockers, 5);
  const recommendedNextMoves: string[] = [];
  if (params.overdueTasks > 0) recommendedNextMoves.push(`Prioritize ${params.overdueTasks} overdue tasks today`);
  if (params.openInterventions > 0) recommendedNextMoves.push(`Close ${params.openInterventions} open intervention requests`);
  if (blockers.length > 0) recommendedNextMoves.push("Turn top blocker into a concrete owner + deadline");
  if (recommendedNextMoves.length === 0) recommendedNextMoves.push("Keep momentum with next milestone follow-up and decision-owner confirmation");

  return {
    command_summary: `Deal room ${params.room.title} has ${params.openTasks} active tasks and ${blockers.length} visible blockers.`,
    current_goal_refinement: params.room.currentGoal || params.room.nextMilestone || "Push the deal to the next verified checkpoint.",
    key_blockers: blockers.length > 0 ? blockers : ["No explicit blocker recorded. Verify decision chain and timeline."],
    recommended_next_moves: recommendedNextMoves,
    manager_attention_reason: params.room.managerAttentionNeeded
      ? "Manager attention flag is on, and this deal needs cross-role support to remove blockers."
      : params.overdueTasks > 1
        ? "Multiple overdue tasks are reducing momentum."
        : "No urgent manager intervention required now.",
    missing_information: ["Decision owner confirmation", "Budget approval timeline", "Checkpoint due-date commitment"]
  };
}

export function buildFallbackThreadSummary(params: {
  threadTitle: string;
  recentMessages: Array<{ body: string; type: string }>;
}): ThreadSummaryResult {
  const comments = params.recentMessages
    .map((item) => item.body.trim())
    .filter((item) => item.length > 0)
    .slice(-3);

  return {
    summary:
      comments.length > 0
        ? `Thread ${params.threadTitle} recently focused on: ${comments.join(" | ")}.`
        : `Thread ${params.threadTitle} has no substantial message yet.`,
    open_questions: comments.length > 0 ? ["Who is owner for next action?", "What is the concrete due date?"] : ["Need initial context and next action owner."],
    recommended_next_action: "Convert the most recent discussion point into one executable task with owner and due date.",
    decision_needed: params.recentMessages.some((item) => item.type === "decision_note")
  };
}

export function buildFallbackDecisionSupport(params: {
  decisionType: string;
  options: string[];
  knownRisks: string[];
}): DecisionSupportResult {
  const options = params.options.length > 0 ? params.options : ["Option A", "Option B"];
  const recommended = options[0];
  return {
    options_assessment: options.map((option, index) => ({
      option,
      pros: index === 0 ? ["Fast to execute", "Lower coordination overhead"] : ["Potentially better upside if assumptions hold"],
      cons: index === 0 ? ["May need later adjustment"] : ["Higher uncertainty", "Requires stronger internal alignment"],
      risk_level: index === 0 ? "medium" : "high"
    })),
    recommended_option: recommended,
    pros_cons: {
      pros: ["Keeps deal momentum", "Improves execution clarity"],
      cons: toShortList(params.knownRisks, 3)
    },
    caution_notes: params.knownRisks.length > 0 ? toShortList(params.knownRisks, 4) : ["Missing pricing boundary and approval timing details."],
    followup_actions: ["Document selected option in decision record", "Create linked execution task", "Set checkpoint due date"]
  };
}

export function buildFallbackInterventionRecommendation(params: {
  managerAttentionNeeded: boolean;
  blockerCount: number;
  overdueTaskCount: number;
}): InterventionRecommendationResult {
  const shouldIntervene = params.managerAttentionNeeded || params.blockerCount >= 2 || params.overdueTaskCount >= 2;
  return {
    whether_to_intervene: shouldIntervene,
    why_now: shouldIntervene
      ? `Deal has ${params.blockerCount} blocker(s) and ${params.overdueTaskCount} overdue task(s), so intervention timing is critical.`
      : "Current execution is stable and can continue with sales owner follow-through.",
    intervention_goal: shouldIntervene ? "Remove blockers and secure the next milestone commitment." : "Keep monitoring and support on-demand.",
    suggested_manager_action: shouldIntervene
      ? ["Join one strategic customer call", "Align pricing/contract boundaries internally", "Assign owner for unresolved blocker"]
      : ["Review deal status in next cadence check-in"],
    expected_shift: shouldIntervene
      ? ["Shorter decision cycle", "Clear owner and due date", "Reduced stall risk"]
      : ["Stable execution rhythm maintained"]
  };
}

export function buildFallbackDealPlaybookMapping(params: {
  room: Pick<DealRoom, "id" | "title" | "currentGoal">;
  playbooks: PlaybookWithEntries[];
}): DealPlaybookMappingResult {
  const relevant = params.playbooks.slice(0, 3).map((item, index) => ({
    playbook_id: item.playbook.id,
    title: item.playbook.title,
    applicability_score: Math.max(0.55, 0.88 - index * 0.12),
    applicability_reason: `Playbook ${item.playbook.playbookType} is aligned with current deal signals and goal context.`,
    suggested_application: item.entries.slice(0, 2).map((entry) => entry.entryTitle)
  }));

  return {
    relevant_playbooks: relevant.length > 0
      ? relevant
      : [
          {
            playbook_id: null,
            title: "Fallback Deal Execution Pattern",
            applicability_score: 0.58,
            applicability_reason: "No active playbook was found; fallback pattern keeps execution structured.",
            suggested_application: ["Clarify next checkpoint owner", "Close one blocker within 24h", "Document decision context before next call"]
          }
        ],
    applicability_reason: `Mapped ${relevant.length} playbook(s) for deal ${params.room.title}.`,
    suggested_application:
      relevant.length > 0 ? relevant.flatMap((item) => item.suggested_application).slice(0, 4) : ["Adopt fallback deal execution checklist"]
  };
}

