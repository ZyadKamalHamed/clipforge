// The window's dialogs, on Radix: the focus stays inside while one is open, Esc closes it, and the focus
// goes back to what opened it.

import { X } from "lucide-react";
import { Dialog as Base } from "radix-ui";
import type { ReactElement, ReactNode } from "react";

type Props = {
  title: string;
  description?: string;
  trigger?: ReactElement; // what opens it, when it isn't opened from code
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  wide?: boolean; // for a table or a few columns of fields
  steps?: ReactNode; // a dialog of steps: they and the description stay at the top with the title
  children: ReactNode;
};

export function Dialog({ title, description, trigger, open, onOpenChange, wide, steps, children }: Props) {
  // Without a description the dialog mustn't point at one, or Radix would name a missing element.
  const described = description ? {} : { "aria-describedby": undefined };
  const about = description && <Base.Description className="text-sm text-muted">{description}</Base.Description>;
  return (
    <Base.Root open={open} onOpenChange={onOpenChange}>
      {trigger && <Base.Trigger asChild>{trigger}</Base.Trigger>}
      <Base.Portal>
        <Base.Overlay className="fixed inset-0 z-40 bg-black/50" />
        {/* Only the middle scrolls: the title (and the steps) stay at the top and a DialogFooter at the
            bottom, and the scroll padding keeps a focused field clear of both. */}
        <Base.Content
          {...described}
          className={
            "fixed top-1/2 left-1/2 z-50 flex max-h-[85vh] -translate-x-1/2 -translate-y-1/2 flex-col " +
            (steps ? "scroll-pt-36 " : "scroll-pt-16 ") +
            "scroll-pb-20 overflow-y-auto rounded-card border border-line bg-panel shadow-2xl " +
            (wide ? "w-[min(60rem,calc(100vw-2rem))]" : "w-[min(32rem,calc(100vw-2rem))]")
          }
        >
          <div className="sticky top-0 z-10 flex flex-col gap-3 border-b border-line bg-panel px-5 pt-5 pb-4">
            <div className="flex items-start justify-between gap-4">
              <Base.Title className="text-base font-semibold">{title}</Base.Title>
              <Base.Close asChild>
                <button
                  type="button"
                  aria-label="Close"
                  className="-m-1 rounded-control p-1 text-muted hover:bg-panel-2 hover:text-fg"
                >
                  <X aria-hidden size={18} />
                </button>
              </Base.Close>
            </div>
            {steps && about}
            {steps}
          </div>
          <div className="flex flex-col gap-4 px-5 pt-4 pb-5">
            {!steps && about}
            {children}
          </div>
        </Base.Content>
      </Base.Portal>
    </Base.Root>
  );
}

// The dialog's buttons, last inside its form or body: they stay in sight at the bottom while the middle scrolls.
export function DialogFooter({ children }: { children: ReactNode }) {
  return (
    <div className="sticky bottom-0 z-10 -mx-5 -mb-5 flex flex-wrap items-center justify-end gap-2 border-t border-line bg-panel px-5 py-4">
      {children}
    </div>
  );
}
