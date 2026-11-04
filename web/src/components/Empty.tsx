// What a page shows when there's nothing in it: what it's for, and the one thing to do next.

import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

type Props = { icon: LucideIcon; title: string; level?: 1 | 2; children?: ReactNode; action?: ReactNode };

export function Empty({ icon: Icon, title, level = 2, children, action }: Props) {
  const Heading = level === 1 ? "h1" : "h2";
  return (
    <div className="mx-auto flex max-w-md flex-col items-center gap-3 py-16 text-center">
      <span className="grid size-12 place-items-center rounded-full bg-panel-2 text-muted">
        <Icon aria-hidden size={22} />
      </span>
      <Heading className="text-lg font-semibold">{title}</Heading>
      {children && <div className="text-sm text-muted">{children}</div>}
      {action}
    </div>
  );
}
