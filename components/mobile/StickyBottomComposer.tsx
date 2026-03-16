"use client";

import { cn } from "@/lib/utils";

export function StickyBottomComposer(props: {
  children: React.ReactNode;
  className?: string;
}): JSX.Element {
  return (
    <div
      className={cn(
        "sticky bottom-16 z-30 rounded-xl border bg-white/95 p-3 shadow-sm backdrop-blur lg:bottom-0 lg:static lg:border-transparent lg:bg-transparent lg:p-0 lg:shadow-none",
        props.className
      )}
    >
      {props.children}
    </div>
  );
}
