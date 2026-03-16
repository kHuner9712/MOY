"use client";

import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";

export function MobileActionSheet(props: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <Sheet open={props.open} onOpenChange={props.onOpenChange}>
      <SheetContent side="right" className="w-full border-l-0 p-0 sm:max-w-none">
        <SheetHeader className="border-b px-4 py-3">
          <SheetTitle className="text-base">{props.title}</SheetTitle>
        </SheetHeader>
        <div className="max-h-[70vh] overflow-y-auto px-4 py-3">{props.children}</div>
      </SheetContent>
    </Sheet>
  );
}
