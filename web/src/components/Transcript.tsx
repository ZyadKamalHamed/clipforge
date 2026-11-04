// The clip's words with 30 s either side: words outside the clip dimmed, struck words crossed out, fillers
// marked and the word being said lit. Two handles mark the clip's start and end; dragged, they snap to
// whole words, and the arrow keys move them a word at a time.

import { type KeyboardEvent, useEffect, useRef, useState } from "react";

import type { Range } from "../lib/editor.ts";
import type { TranscriptWord } from "../types.ts";
import { clock } from "./Player.tsx";

type Props = {
  words: TranscriptWord[];
  range: Range;
  struck: ReadonlySet<number>;
  fixes: Readonly<Record<string, string>>;
  selection: [number, number] | null;
  playing: number | null;
  onWord: (i: number, extend: boolean) => void;
  onRange: (range: Range) => void;
};

const NEAR = 0.001;

export function inRange(w: { start: number; end: number }, [start, end]: Range): boolean {
  return w.start >= start - NEAR && w.end <= end + NEAR;
}

export function Transcript({ words, range, struck, fixes, selection, playing, onWord, onRange }: Props) {
  const inside = words.filter((w) => inRange(w, range));
  const first = inside[0]?.i ?? null;
  const last = inside.at(-1)?.i ?? null;
  const focusable = selection?.[0] ?? first ?? words[0]?.i;
  const box = useRef<HTMLDivElement>(null);
  // The handles move to another word as the range changes, so the drag and the focus are kept up here.
  const [drag, setDrag] = useState<Side | null>(null);
  const refocus = useRef<Side | null>(null);
  const latest = useRef({ words, range, onRange });
  useEffect(() => {
    latest.current = { words, range, onRange };
    if (refocus.current) box.current?.querySelector<HTMLElement>(`[data-handle="${refocus.current}"]`)?.focus();
    refocus.current = null;
  });
  useEffect(() => {
    if (!drag) return;
    const move = (e: PointerEvent) => {
      const hit = document.elementsFromPoint(e.clientX, e.clientY).find((el) => el instanceof HTMLElement && el.dataset.word);
      const i = hit instanceof HTMLElement ? Number(hit.dataset.word) : NaN;
      const { words: all, range: now, onRange: set } = latest.current;
      const w = all.find((x) => x.i === i);
      const next = w && snap(drag, w, now);
      if (next) set(next);
    };
    const stop = () => setDrag(null);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    };
  }, [drag]);

  function step(which: Side, by: number) {
    const inside = words.filter((w) => inRange(w, range));
    const edge = which === "start" ? inside[0] : inside.at(-1);
    const next = edge && words[words.indexOf(edge) + by];
    const moved = next && snap(which, next, range);
    if (!moved) return;
    refocus.current = which;
    onRange(moved);
  }
  const handle = (which: Side) => (
    <Handle which={which} words={words} range={range} onGrab={() => setDrag(which)} onStep={(by) => step(which, by)} />
  );

  function focusWord(i: number) {
    box.current?.querySelector<HTMLElement>(`[data-word="${i}"]`)?.focus();
  }

  function onWordKey(e: KeyboardEvent<HTMLButtonElement>, k: number) {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!step || e.altKey || e.metaKey || e.ctrlKey) return;
    const next = words[k + step];
    if (!next) return;
    e.preventDefault();
    onWord(next.i, e.shiftKey);
    focusWord(next.i);
  }

  return (
    <div ref={box} role="group" aria-label="Transcript" className="text-sm leading-8">
      {words.map((w, k) => {
        const out = !inRange(w, range);
        const cut = struck.has(w.i);
        const shown = fixes[String(w.i)] ?? w.text;
        const picked = selection !== null && w.i >= selection[0] && w.i <= selection[1];
        const extra = [cut && "cut", w.filler && "filler"].filter(Boolean);
        return (
          <span key={w.i}>
            {w.i === first && handle("start")}
            <button
              type="button"
              data-word={w.i}
              data-outside={out ? "yes" : undefined}
              aria-pressed={picked}
              aria-current={w.i === playing ? "true" : undefined}
              aria-label={extra.length ? [shown, ...extra].join(", ") : undefined}
              tabIndex={w.i === focusable ? 0 : -1}
              dir="auto"
              onClick={(e) => onWord(w.i, e.shiftKey)}
              onKeyDown={(e) => onWordKey(e, k)}
              className={
                "rounded px-0.5 focus-visible:outline-2 focus-visible:outline-accent " +
                (out ? "text-faint " : "text-fg ") +
                (cut ? "line-through decoration-bad decoration-2 opacity-60 " : "") +
                (w.filler ? "underline decoration-warn decoration-dotted underline-offset-4 " : "") +
                (picked ? "bg-accent/25 " : w.i === playing ? "bg-accent-strong/40 " : "hover:bg-panel-2")
              }
            >
              {shown}
            </button>
            {w.i === last && handle("end")}{" "}
          </span>
        );
      })}
    </div>
  );
}

type Side = "start" | "end";

// The range with its start (or end) at word ``w``, or null where that would leave no clip.
function snap(which: Side, w: TranscriptWord, [start, end]: Range): Range | null {
  if (which === "start") return w.start < end ? [w.start, end] : null;
  return w.end > start ? [start, w.end] : null;
}

type HandleProps = {
  which: Side;
  words: TranscriptWord[];
  range: Range;
  onGrab: () => void;
  onStep: (by: number) => void;
};

function Handle({ which, words, range, onGrab, onStep }: HandleProps) {
  const value = which === "start" ? range[0] : range[1];
  const lo = words[0]?.start ?? 0;
  const hi = words.at(-1)?.end ?? value;

  function onKey(e: KeyboardEvent<HTMLSpanElement>) {
    const by = e.key === "ArrowRight" || e.key === "ArrowUp" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowDown" ? -1 : 0;
    if (!by || e.altKey || e.metaKey || e.ctrlKey) return;
    e.preventDefault();
    onStep(by);
  }

  return (
    <span className="relative inline-block h-5 w-0 align-middle">
      <span
        role="slider"
        tabIndex={0}
        data-handle={which}
        aria-label={which === "start" ? "Clip start" : "Clip end"}
        aria-orientation="horizontal"
        aria-valuemin={lo}
        aria-valuemax={hi}
        aria-valuenow={value}
        aria-valuetext={clock(value)}
        onPointerDown={(e) => {
          e.preventDefault();
          onGrab();
        }}
        onKeyDown={onKey}
        className={
          "absolute -top-0.5 -left-1.5 h-6 w-3 cursor-ew-resize touch-none rounded-sm bg-accent " +
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent " +
          (which === "start" ? "rounded-r-none" : "rounded-l-none")
        }
      />
    </span>
  );
}
