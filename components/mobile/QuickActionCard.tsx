import { Card, CardContent } from "@/components/ui/card";

export function QuickActionCard(props: {
  title: string;
  subtitle?: string;
  right?: React.ReactNode;
  children?: React.ReactNode;
}): JSX.Element {
  return (
    <Card className="rounded-xl border-slate-200">
      <CardContent className="space-y-2 p-3">
        <div className="flex items-start justify-between gap-2">
          <div>
            <p className="text-sm font-semibold text-slate-900">{props.title}</p>
            {props.subtitle ? <p className="text-xs text-muted-foreground">{props.subtitle}</p> : null}
          </div>
          {props.right ? <div>{props.right}</div> : null}
        </div>
        {props.children}
      </CardContent>
    </Card>
  );
}
