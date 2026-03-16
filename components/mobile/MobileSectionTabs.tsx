"use client";

import { cn } from "@/lib/utils";

export function MobileSectionTabs<T extends string>(props: {
  value: T;
  onChange: (value: T) => void;
  options: Array<{ value: T; label: string }>;
}): JSX.Element {
  return (
    <div className="mb-3 flex gap-2 overflow-x-auto pb-1">
      {props.options.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => props.onChange(option.value)}
          className={cn(
            "whitespace-nowrap rounded-full border px-3 py-1.5 text-xs font-medium",
            props.value === option.value ? "border-sky-300 bg-sky-100 text-sky-800" : "border-slate-200 bg-white text-slate-600"
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
