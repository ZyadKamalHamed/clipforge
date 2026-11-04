// What went wrong in full, kept behind "Show details" under the plain sentence, beside the failure's other
// buttons (its Retry, say).

import { type ReactNode, useId, useState } from "react";

import { Button } from "./Button.tsx";

export function Details({ text, children }: { text: string; children?: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        {children}
        <Button variant="ghost" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
          {open ? "Hide details" : "Show details"}
        </Button>
      </div>
      <pre
        id={id}
        hidden={!open}
        dir="auto"
        className="max-h-64 overflow-auto rounded-control border border-line bg-bg p-3 font-mono text-xs whitespace-pre-wrap text-muted"
      >
        {text}
      </pre>
    </div>
  );
}
