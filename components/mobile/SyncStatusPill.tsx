import { Badge } from "@/components/ui/badge";

export function SyncStatusPill(props: { status: "pending" | "synced" | "failed" | "discarded" }): JSX.Element {
  if (props.status === "synced") return <Badge variant="secondary">已同步</Badge>;
  if (props.status === "failed") return <Badge variant="destructive">同步失败</Badge>;
  if (props.status === "discarded") return <Badge variant="outline">已丢弃</Badge>;
  return <Badge variant="outline">待同步</Badge>;
}
