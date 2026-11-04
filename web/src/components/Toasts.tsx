// Short confirmations and failures in one polite live region (SPEC 14.1: never a browser alert). A toast
// goes after 5 s (8 s for a failure, 10 s with an action, never with ms 0) and waits while the pointer or
// the focus is on the toasts. One sent again with the same id takes the old one's place, which is how an
// upload's progress moves.

import { CircleCheck, Info, type LucideIcon, TriangleAlert, X } from "lucide-react";
import { createContext, type ReactNode, useContext, useMemo, useRef, useState } from "react";

export type ToastKind = "info" | "ok" | "warn" | "bad";

export type ToastOptions = {
  text: string;
  kind?: ToastKind;
  subject?: string; // what it's about (a file or a link), on its own line and in its own direction
  action?: { label: string; run: () => void };
  progress?: number; // 0 to 1, shown as a bar
  ms?: number;
  id?: string;
};

export type Toaster = ((options: ToastOptions) => string) & { dismiss: (id: string) => void };

type Shown = ToastOptions & { id: string; kind: ToastKind };
type Timer = { left: number; since: number; handle: ReturnType<typeof setTimeout> | null };

const MAX_SHOWN = 4;
const ICONS: Record<ToastKind, LucideIcon> = { info: Info, ok: CircleCheck, warn: TriangleAlert, bad: TriangleAlert };
const TONES: Record<ToastKind, string> = { info: "text-info", ok: "text-ok", warn: "text-warn", bad: "text-bad" };

const Context = createContext<Toaster | null>(null);

export function useToast(): Toaster {
  const toaster = useContext(Context);
  if (!toaster) throw new Error("useToast needs a ToastProvider around it");
  return toaster;
}

function lasting(options: ToastOptions): number {
  if (options.ms !== undefined) return options.ms;
  if (options.action) return 10_000;
  return options.kind === "bad" ? 8_000 : 5_000;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [shown, setShown] = useState<Shown[]>([]);
  const list = useRef<Shown[]>([]);
  const timers = useRef(new Map<string, Timer>());
  const held = useRef({ hover: false, focus: false });
  const count = useRef(0);

  const { toaster, hold } = useMemo(() => {
    const paused = () => held.current.hover || held.current.focus;
    const publish = (next: Shown[]) => {
      list.current = next;
      setShown(next);
    };
    const stop = (id: string) => {
      const timer = timers.current.get(id);
      if (timer?.handle) clearTimeout(timer.handle);
      timers.current.delete(id);
    };
    const dismiss = (id: string) => {
      stop(id);
      publish(list.current.filter((s) => s.id !== id));
    };
    const run = (id: string, timer: Timer) => {
      timer.since = Date.now();
      timer.handle = setTimeout(() => dismiss(id), Math.max(0, timer.left));
    };
    const toast = (options: ToastOptions): string => {
      const id = options.id ?? `toast-${(count.current += 1)}`;
      stop(id);
      const ms = lasting(options);
      if (ms > 0) {
        const timer: Timer = { left: ms, since: 0, handle: null };
        timers.current.set(id, timer);
        if (!paused()) run(id, timer);
      }
      const item: Shown = { ...options, id, kind: options.kind ?? "info" };
      if (list.current.some((s) => s.id === id)) {
        publish(list.current.map((s) => (s.id === id ? item : s)));
      } else {
        const all = [...list.current, item];
        for (const old of all.slice(0, -MAX_SHOWN)) stop(old.id);
        publish(all.slice(-MAX_SHOWN));
      }
      return id;
    };
    const hold = (part: "hover" | "focus", on: boolean) => {
      const was = paused();
      held.current[part] = on;
      if (paused() === was) return;
      for (const [id, timer] of timers.current) {
        if (!was) {
          if (timer.handle) clearTimeout(timer.handle);
          timer.handle = null;
          timer.left -= Date.now() - timer.since;
        } else {
          run(id, timer);
        }
      }
    };
    return { toaster: Object.assign(toast, { dismiss }) as Toaster, hold };
  }, []);

  const close = (id: string) => {
    toaster.dismiss(id);
    hold("focus", false); // the focus was on the toast that just went
  };

  return (
    <Context value={toaster}>
      {children}
      <section
        role="status"
        aria-live="polite"
        aria-label="Notifications"
        className="pointer-events-none fixed right-4 bottom-4 z-50 flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2"
        onMouseEnter={() => hold("hover", true)}
        onMouseLeave={() => hold("hover", false)}
        onFocus={() => hold("focus", true)}
        onBlur={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) hold("focus", false);
        }}
      >
        {shown.map((s) => (
          <ToastCard key={s.id} toast={s} onClose={() => close(s.id)} />
        ))}
      </section>
    </Context>
  );
}

function ToastCard({ toast, onClose }: { toast: Shown; onClose: () => void }) {
  const Icon = ICONS[toast.kind];
  return (
    <div className="pointer-events-auto flex items-start gap-3 rounded-card border border-line bg-panel p-3 text-sm shadow-lg">
      <Icon aria-hidden size={18} className={`mt-0.5 shrink-0 ${TONES[toast.kind]}`} />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        {toast.subject && <bdi className="block truncate font-medium">{toast.subject}</bdi>}
        <p>{toast.text}</p>
        {toast.progress !== undefined && (
          <div
            role="progressbar"
            aria-label={toast.subject ? `${toast.text} ${toast.subject}` : toast.text}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(toast.progress * 100)}
            className="mt-1 h-1 overflow-hidden rounded-full bg-line"
          >
            <div className="h-full bg-accent" style={{ width: `${toast.progress * 100}%` }} />
          </div>
        )}
        {toast.action && (
          <button
            type="button"
            className="self-start rounded-sm font-medium text-fg underline underline-offset-2 hover:decoration-2"
            onClick={() => {
              toast.action?.run();
              onClose();
            }}
          >
            {toast.action.label}
          </button>
        )}
      </div>
      <button
        type="button"
        aria-label="Dismiss"
        className="-m-1 rounded-control p-1 text-muted hover:bg-panel-2 hover:text-fg"
        onClick={onClose}
      >
        <X aria-hidden size={16} />
      </button>
    </div>
  );
}
