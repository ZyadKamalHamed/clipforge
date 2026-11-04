// Tabs on Radix: the arrow keys move between them, and only the open tab's panel is in the page.

import { Tabs as Base } from "radix-ui";
import type { ReactNode } from "react";

type Props<T extends string> = {
  label: string;
  value: T;
  onChange: (value: T) => void;
  tabs: { value: T; label: string; content: () => ReactNode }[];
};

export function Tabs<T extends string>({ label, value, onChange, tabs }: Props<T>) {
  return (
    <Base.Root value={value} onValueChange={(v) => onChange(v as T)} className="flex min-h-0 flex-col">
      <Base.List aria-label={label} className="flex gap-1 border-b border-line">
        {tabs.map((tab) => (
          <Base.Trigger
            key={tab.value}
            value={tab.value}
            className={
              "-mb-px border-b-2 border-transparent px-3 py-2 text-sm text-muted hover:text-fg " +
              "data-[state=active]:border-accent data-[state=active]:text-fg focus-visible:outline-2 " +
              "focus-visible:outline-offset-[-2px] focus-visible:outline-accent"
            }
          >
            {tab.label}
          </Base.Trigger>
        ))}
      </Base.List>
      {tabs.map((tab) => (
        <Base.Content key={tab.value} value={tab.value} className="min-h-0 flex-1 overflow-y-auto pt-4 focus-visible:outline-none">
          {tab.value === value && tab.content()}
        </Base.Content>
      ))}
    </Base.Root>
  );
}
