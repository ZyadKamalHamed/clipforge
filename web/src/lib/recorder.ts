// The Sound tab's voiceover recorder: asks Chrome for the microphone, records one take of up to 3 minutes
// and lets the microphone go as soon as it stops. Its parts come in from outside, so the tests drive it
// with a fake MediaRecorder and timer.

export const RECORD_MAX_S = 180;
export const DENIED =
  "ClipForge can't use the microphone. Allow it from the icon in Chrome's address bar, or choose a recording file.";

// The recording files the engine takes (clipforge/voiceover.py EXTS), for the file chooser.
export const VOICE_ACCEPT = [
  "webm",
  "ogg",
  "m4a",
  "mp4",
  "mp3",
  "wav",
  "aac",
  "caf",
]
  .map((t) => `.${t}`)
  .join(",");

export type RecorderState =
  "idle" | "asking" | "recording" | "stopped" | "denied";

// A file name for a take, from the type the recorder gave it.
export function takeName(type: string): string {
  if (type.startsWith("audio/ogg")) return "voiceover.ogg";
  if (type.startsWith("audio/mp4")) return "voiceover.m4a";
  return "voiceover.webm";
}

function clock(seconds: number): string {
  const s = Math.floor(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// The timer while recording, as "0:07 of 3:00".
export function timerText(seconds: number): string {
  return `${clock(seconds)} of ${clock(RECORD_MAX_S)}`;
}

export type RecorderLike = {
  mimeType: string;
  ondataavailable: ((e: { data: Blob }) => void) | null;
  onstop: (() => void) | null;
  start(): void;
  stop(): void;
};

export type RecorderParts = {
  getUserMedia: () => Promise<MediaStream>;
  makeRecorder: (stream: MediaStream) => RecorderLike;
  setTimer: (fn: () => void, ms: number) => unknown;
  clearTimer: (handle: unknown) => void;
  now: () => number; // in milliseconds
};

// The browser's own parts, for the page.
export function browserParts(): RecorderParts {
  return {
    getUserMedia: () => navigator.mediaDevices.getUserMedia({ audio: true }),
    makeRecorder: (stream) => {
      const rec = new MediaRecorder(stream);
      const like: RecorderLike = {
        get mimeType() {
          return rec.mimeType;
        },
        ondataavailable: null,
        onstop: null,
        start: () => rec.start(),
        stop: () => rec.stop(),
      };
      rec.ondataavailable = (e) => like.ondataavailable?.(e);
      rec.onstop = () => like.onstop?.();
      return like;
    },
    setTimer: (fn, ms) => window.setTimeout(fn, ms),
    clearTimer: (handle) => window.clearTimeout(handle as number),
    now: () => performance.now(),
  };
}

export function recorder(parts: RecorderParts) {
  const listeners = new Set<() => void>();
  let state: RecorderState = "idle";
  let take: Blob | null = null;
  let stream: MediaStream | null = null;
  let active: RecorderLike | null = null;
  let timer: unknown = null;
  let startedAt = 0;
  let version = 0;
  let disposed = false;

  function set(next: RecorderState) {
    state = next;
    version += 1;
    if (!disposed) for (const listener of listeners) listener();
  }

  function letGo() {
    if (timer !== null) parts.clearTimer(timer);
    timer = null;
    for (const track of stream?.getTracks() ?? []) track.stop();
    stream = null;
  }

  function stop() {
    if (state !== "recording" || active === null) return;
    active.stop();
  }

  return {
    async start() {
      if (state === "asking" || state === "recording" || disposed) return;
      take = null;
      set("asking");
      let given: MediaStream;
      try {
        given = await parts.getUserMedia();
      } catch {
        set(disposed ? "idle" : "denied");
        return;
      }
      stream = given;
      if (disposed) {
        letGo();
        set("idle");
        return;
      }
      const chunks: Blob[] = [];
      const rec = parts.makeRecorder(given);
      rec.ondataavailable = (e) => {
        if (e.data.size > 0) chunks.push(e.data);
      };
      rec.onstop = () => {
        letGo();
        active = null;
        if (disposed) return;
        take = new Blob(chunks, { type: rec.mimeType });
        set("stopped");
      };
      active = rec;
      rec.start();
      startedAt = parts.now();
      timer = parts.setTimer(stop, RECORD_MAX_S * 1000);
      set("recording");
    },
    stop,
    // Record again: the take is let go.
    reset() {
      if (state === "asking" || state === "recording") return;
      take = null;
      set("idle");
    },
    // The tab or the editor closing: the microphone is let go and any take is dropped.
    dispose() {
      disposed = true;
      take = null;
      if (active !== null) {
        active.onstop = null;
        active.stop();
        active = null;
      }
      letGo();
    },
    state: () => state,
    take: () => take,
    // Seconds since recording started.
    elapsed: () =>
      state === "recording" ? (parts.now() - startedAt) / 1000 : 0,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => void listeners.delete(listener);
    },
    version: () => version,
  };
}

export type Recorder = ReturnType<typeof recorder>;
