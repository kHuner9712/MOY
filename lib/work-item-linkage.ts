export interface FollowupLinkableTask {
  id: string;
  customer_id: string | null;
  status: string;
  work_type: string;
}

export function pickAutoCompletableTaskIdsAfterFollowup(params: {
  tasks: FollowupLinkableTask[];
  customerId: string;
}): string[] {
  return params.tasks
    .filter((task) => task.customer_id === params.customerId)
    .filter((task) => ["todo", "in_progress", "snoozed"].includes(task.status))
    .filter((task) => ["followup_call", "review_customer"].includes(task.work_type))
    .map((task) => task.id);
}
