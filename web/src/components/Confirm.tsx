// Asking before something that can't be undone, in the window's own dialog (SPEC 14.1: never the
// browser's). useConfirm() gives a function that shows the question and resolves true only on its action.

import { createContext, type ReactNode, useContext, useMemo, useRef, useState } from "react";

import { Button } from "./Button.tsx";
import { Dialog, DialogFooter } from "./Dialog.tsx";

// ``keep`` is what the No button says, "Cancel" unless the question is about cancelling itself.
export type Ask = { title: string; description: string; action: string; danger?: boolean; keep?: string };

type Asker = (ask: Ask) => Promise<boolean>;

const Context = createContext<Asker | null>(null);

export function useConfirm(): Asker {
  const asker = useContext(Context);
  if (!asker) throw new Error("useConfirm needs a ConfirmProvider around it");
  return asker;
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [ask, setAsk] = useState<Ask | null>(null);
  const answer = useRef<((yes: boolean) => void) | null>(null);

  function settle(yes: boolean) {
    answer.current?.(yes);
    answer.current = null;
    setAsk(null);
  }

  const asker = useMemo<Asker>(
    () => (next) =>
      new Promise<boolean>((resolve) => {
        answer.current?.(false); // a new question replaces one still open, which counts as a no
        answer.current = resolve;
        setAsk(next);
      }),
    [],
  );

  return (
    <Context value={asker}>
      {children}
      <Dialog
        title={ask?.title ?? ""}
        description={ask?.description}
        open={ask !== null}
        onOpenChange={(open) => {
          if (!open) settle(false);
        }}
      >
        <DialogFooter>
          <Button onClick={() => settle(false)}>{ask?.keep ?? "Cancel"}</Button>
          <Button variant={ask?.danger ? "danger" : "primary"} onClick={() => settle(true)}>
            {ask?.action}
          </Button>
        </DialogFooter>
      </Dialog>
    </Context>
  );
}
