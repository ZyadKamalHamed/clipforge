// The Sound tab's voiceover: your own words over the clip, recorded here (Chrome asks for the microphone the
// first time) or chosen as a recording file. A take is saved and set as soon as you use it, and the clip
// renders again with the speech ducked under it; where it starts waits for the render like the other edits.
// A voiceover gets no captions.

import { Mic, Square, Upload } from "lucide-react";
import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { api, sentence, upload } from "../api.ts";
import type { Editing } from "../lib/editor.ts";
import {
  browserParts,
  DENIED,
  recorder,
  takeName,
  timerText,
  VOICE_ACCEPT,
} from "../lib/recorder.ts";
import type { ClipDetail } from "../types.ts";
import { Button } from "./Button.tsx";
import { FieldError } from "./Field.tsx";
import { useToast } from "./Toasts.tsx";

const SECTION =
  "flex flex-col gap-3 border-t border-line pt-4 first:border-t-0 first:pt-0";
const NUMBER =
  "w-24 rounded-control border border-line bg-panel-2 px-3 py-1.5 text-sm text-fg tabular-nums " +
  "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

const enc = encodeURIComponent;

// A link to play ``blob`` while it is on the page.
function useBlobUrl(blob: Blob | null): string | null {
  const url = useMemo(() => (blob ? URL.createObjectURL(blob) : null), [blob]);
  useEffect(() => () => void (url && URL.revokeObjectURL(url)), [url]);
  return url;
}

type Props = { editing: Editing; onSaved: (updated: ClipDetail) => void };

export function Voiceover({ editing, onSaved }: Props) {
  const { detail, value, change } = editing;
  const toast = useToast();
  const headingId = useId();
  const startId = useId();
  const picker = useRef<HTMLInputElement>(null);
  const rec = useMemo(() => recorder(browserParts()), []);
  useEffect(() => () => rec.dispose(), [rec]);
  useSyncExternalStore(rec.subscribe, rec.version);
  const state = rec.state();
  const take = rec.take();
  const takeUrl = useBlobUrl(take);
  const [elapsed, setElapsed] = useState(0);
  const [sending, setSending] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    if (state !== "recording") return;
    setElapsed(0);
    const timer = window.setInterval(() => setElapsed(rec.elapsed()), 250);
    return () => window.clearInterval(timer);
  }, [state, rec]);

  const file = value<string | null>("voiceover");
  const at = value<number | null>("voiceover_at") ?? 0;
  const length = Math.round((detail.end_s - detail.start_s) * 10) / 10;

  async function send(blob: Blob, name: string) {
    setSending(true);
    setProblem(null);
    try {
      const updated = await upload<ClipDetail>(
        `/api/clips/${enc(detail.id)}/voiceover?name=${enc(name)}`,
        blob,
        {},
        () => {},
      );
      rec.reset();
      onSaved(updated);
      toast({
        text: "Voiceover saved. The clip renders again with it.",
        kind: "ok",
      });
    } catch (err) {
      setProblem(sentence(err));
    } finally {
      setSending(false);
    }
  }

  async function remove() {
    setSending(true);
    setProblem(null);
    try {
      onSaved(
        await api<ClipDetail>(`/api/clips/${enc(detail.id)}/voiceover`, {
          method: "DELETE",
        }),
      );
      toast({
        text: "Voiceover removed. The clip renders again without it.",
        kind: "ok",
      });
    } catch (err) {
      setProblem(sentence(err));
    } finally {
      setSending(false);
    }
  }

  return (
    <section className={SECTION} aria-labelledby={headingId}>
      <div className="flex flex-col gap-1">
        <h3 id={headingId} className="text-sm font-semibold">
          Voiceover
        </h3>
        <p className="text-xs text-muted">
          Your own words over the clip. The speech drops under them while you
          talk.
        </p>
      </div>
      {file && (
        <div className="flex flex-col gap-2">
          <audio
            controls
            preload="metadata"
            aria-label="Your voiceover"
            src={`/api/clips/${enc(detail.id)}/voiceover/${enc(file)}`}
            className="w-full"
          />
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex flex-col gap-1">
              <label
                htmlFor={startId}
                className="text-xs font-medium text-muted"
              >
                Starts at
              </label>
              <div className="flex items-center gap-2">
                <input
                  id={startId}
                  type="number"
                  min={0}
                  max={length}
                  step={0.1}
                  value={at}
                  onChange={(e) => {
                    const seconds = Number(e.target.value);
                    if (e.target.value !== "" && Number.isFinite(seconds))
                      change("voiceover_at", Math.round(seconds * 10) / 10);
                  }}
                  className={NUMBER}
                />
                <span className="text-xs text-faint">
                  s into the clip, up to {length.toFixed(1)}
                </span>
              </div>
            </div>
            <Button
              variant="ghost"
              onClick={() => void remove()}
              disabled={sending}
            >
              Remove
            </Button>
          </div>
        </div>
      )}
      {state === "stopped" && take && takeUrl ? (
        <div className="flex flex-col gap-2">
          <audio
            controls
            aria-label="Your new take"
            src={takeUrl}
            className="w-full"
          />
          <div className="flex flex-wrap gap-2">
            <Button
              variant="primary"
              onClick={() => void send(take, takeName(take.type))}
              disabled={sending}
            >
              Use this take
            </Button>
            <Button onClick={() => rec.reset()} disabled={sending}>
              Record again
            </Button>
          </div>
        </div>
      ) : state === "recording" ? (
        <div className="flex items-center gap-3">
          <span role="timer" className="text-sm tabular-nums">
            {timerText(elapsed)}
          </span>
          <Button onClick={() => rec.stop()}>
            <Square aria-hidden size={14} />
            Stop
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button
            onClick={() => void rec.start()}
            disabled={sending || state === "asking"}
          >
            <Mic aria-hidden size={14} />
            Record
          </Button>
          <Button
            onClick={() => picker.current?.click()}
            disabled={sending || state === "asking"}
          >
            <Upload aria-hidden size={14} />
            Choose a file
          </Button>
          <input
            ref={picker}
            type="file"
            accept={VOICE_ACCEPT}
            tabIndex={-1}
            aria-label="Voiceover recording file"
            className="hidden"
            onChange={(e) => {
              const chosen = e.target.files?.[0];
              e.target.value = ""; // so choosing the same file again still counts
              if (chosen) void send(chosen, chosen.name);
            }}
          />
        </div>
      )}
      {state === "asking" && (
        <p className="text-xs text-muted">
          Waiting for Chrome to let ClipForge use the microphone.
        </p>
      )}
      {sending && <p className="text-xs text-muted">Saving the voiceover.</p>}
      {state === "denied" && <FieldError text={DENIED} />}
      {problem && <FieldError text={problem} />}
    </section>
  );
}
