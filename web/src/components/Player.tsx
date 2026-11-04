// The clip's 9:16 player: play, a scrubber, the time, loop and the safe zone. The editor drives the same
// video element from its keys. When the version changes the new one picks up at the same moment, playing
// if the old one was.

import { Pause, Play, Repeat, Scan } from "lucide-react";
import { type ReactNode, type RefObject, useEffect, useLayoutEffect, useRef, useState } from "react";

// SPEC 8.6: what the apps' buttons and captions leave clear, x 90 to 940 and y 270 to 1250 of 1080x1920.
const SAFE = { left: (90 / 1080) * 100, top: (270 / 1920) * 100, width: (850 / 1080) * 100, height: (980 / 1920) * 100 };

type Props = {
  video: RefObject<HTMLVideoElement | null>;
  src: string | null;
  poster: string | null;
  onTime: (t: number) => void;
};

export function clock(seconds: number): string {
  const s = Math.max(0, seconds);
  return `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
}

// Moves the video to ``t``, kept between its start and end.
export function seekTo(v: HTMLVideoElement, t: number) {
  const end = Number.isFinite(v.duration) ? v.duration : t;
  v.currentTime = Math.min(Math.max(0, t), end);
}

export function playPause(v: HTMLVideoElement) {
  if (v.paused) void v.play().catch(() => undefined);
  else v.pause();
}

export function Player({ video, src, poster, onTime }: Props) {
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [loop, setLoop] = useState(true);
  const [safe, setSafe] = useState(false);
  const carry = useRef<{ t: number; playing: boolean } | null>(null);
  const onTimeRef = useRef(onTime);
  useEffect(() => {
    onTimeRef.current = onTime;
  });

  // The page sets the source itself, so the old version's moment is still there to carry over.
  useLayoutEffect(() => {
    const v = video.current;
    if (!v || !src || v.getAttribute("src") === src) return;
    if (v.getAttribute("src")) carry.current = { t: v.currentTime, playing: !v.paused };
    v.setAttribute("src", src);
    v.load();
  }, [src, video]);

  useEffect(() => {
    const v = video.current;
    if (!v) return;
    let frame = 0;
    const show = () => {
      setTime(v.currentTime);
      onTimeRef.current(v.currentTime);
    };
    const tick = () => {
      show();
      frame = requestAnimationFrame(tick);
    };
    const handlers: Record<string, () => void> = {
      loadedmetadata: () => {
        setDuration(v.duration);
        const from = carry.current;
        carry.current = null;
        if (from) {
          seekTo(v, from.t);
          if (from.playing) void v.play().catch(() => undefined);
        }
        show();
      },
      play: () => {
        setPlaying(true);
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(tick);
      },
      pause: () => {
        setPlaying(false);
        cancelAnimationFrame(frame);
        show();
      },
      seeking: show, // the scrubber shows where a seek is going, not where the last one ended
      seeked: show,
      timeupdate: show,
      durationchange: () => setDuration(v.duration),
    };
    for (const [name, fn] of Object.entries(handlers)) v.addEventListener(name, fn);
    return () => {
      cancelAnimationFrame(frame);
      for (const [name, fn] of Object.entries(handlers)) v.removeEventListener(name, fn);
    };
  }, [video]);

  useEffect(() => {
    if (video.current) video.current.loop = loop;
  }, [loop, video]);

  const end = Number.isFinite(duration) ? duration : 0;
  return (
    // As wide as the frame and centred, so a wide column never stretches it out of 9:16. Above the editor's
    // tabs in a narrow window, it stays short enough to leave them in view.
    <div className="mx-auto flex w-fit max-w-full flex-col gap-2">
      <div
        data-testid="player-frame"
        className={
          "relative h-[min(calc(100vh-20rem),26rem)] self-center overflow-hidden rounded-card bg-black " +
          "lg:h-[min(calc(100vh-15rem),40rem)]"
        }
        style={{ aspectRatio: "9 / 16" }}
      >
        <video
          ref={video}
          poster={poster ?? undefined}
          preload="metadata"
          playsInline
          onClick={(e) => playPause(e.currentTarget)}
          className="absolute inset-0 h-full w-full cursor-pointer object-contain"
        />
        {!src && (
          <p className="pointer-events-none absolute inset-0 grid place-items-center p-6 text-center text-sm text-muted">
            No video yet. It plays here once it's rendered.
          </p>
        )}
        {safe && (
          <div
            data-testid="safe-zone"
            aria-hidden
            className="pointer-events-none absolute border-2 border-dashed border-warn/80"
            style={{ left: `${SAFE.left}%`, top: `${SAFE.top}%`, width: `${SAFE.width}%`, height: `${SAFE.height}%` }}
          />
        )}
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          aria-label={playing ? "Pause" : "Play"}
          onClick={() => video.current && playPause(video.current)}
          className="rounded-control p-1.5 text-fg hover:bg-panel-2 focus-visible:outline-2 focus-visible:outline-accent"
        >
          {playing ? <Pause aria-hidden size={18} /> : <Play aria-hidden size={18} />}
        </button>
        <input
          type="range"
          aria-label="Position"
          aria-valuetext={clock(time)}
          min={0}
          max={end || 0}
          step="any"
          value={Math.min(time, end || time)}
          onChange={(e) => video.current && seekTo(video.current, Number(e.target.value))}
          className="min-w-0 flex-1 accent-accent"
        />
        <span className="text-xs whitespace-nowrap text-muted tabular-nums">
          {clock(time)} / {clock(end)}
        </span>
      </div>
      <div className="flex gap-2">
        <Pressed label="Loop" on={loop} onClick={() => setLoop((x) => !x)} icon={<Repeat aria-hidden size={14} />} />
        <Pressed label="Safe zone" on={safe} onClick={() => setSafe((x) => !x)} icon={<Scan aria-hidden size={14} />} />
      </div>
    </div>
  );
}

function Pressed({ label, on, onClick, icon }: { label: string; on: boolean; onClick: () => void; icon: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={
        "inline-flex items-center gap-1.5 rounded-control border px-2.5 py-1 text-xs focus-visible:outline-2 " +
        `focus-visible:outline-offset-2 focus-visible:outline-accent ${on ? "border-accent text-fg" : "border-line text-muted"}`
      }
    >
      {icon}
      {label}
    </button>
  );
}
