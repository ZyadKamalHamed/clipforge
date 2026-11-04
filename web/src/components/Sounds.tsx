// The five sound effects (SPEC 8.6), each with a button that plays it here and shows it playing. The
// editor's Sound tab and the Library both list them.

import { useQuery } from "@tanstack/react-query";
import { Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api, key, sentence } from "../api.ts";
import { lengthText } from "../lib/sounds.ts";
import type { Sound } from "../types.ts";
import { Button } from "./Button.tsx";

export const SOUNDS: Record<string, string> = { whoosh: "Whoosh", pop: "Pop", hit: "Hit", riser: "Riser", click: "Click" };

export function SoundList() {
  const sounds = useQuery({
    queryKey: key.sfx,
    queryFn: ({ signal }) => api<{ sounds: Sound[] }>("/api/library/sfx", { signal }),
  });
  const audio = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);
  useEffect(() => () => audio.current?.pause(), []);

  function hear(sound: Sound) {
    audio.current?.pause();
    const next = new Audio(sound.url);
    audio.current = next;
    const done = () => {
      if (audio.current === next) setPlaying(null);
    };
    for (const name of ["ended", "pause", "error"]) next.addEventListener(name, done);
    setPlaying(sound.kind);
    void next.play().catch(done);
  }

  if (sounds.isError) return <p className="text-sm text-bad">{sentence(sounds.error)}</p>;
  return (
    <ul className="flex flex-col gap-1">
      {(sounds.data?.sounds ?? []).map((sound) => (
        <li key={sound.kind} className="flex items-center justify-between gap-3 rounded-control bg-panel-2 px-3 py-1.5 text-sm">
          <span>{SOUNDS[sound.kind] ?? sound.kind}</span>
          <Button
            variant="ghost"
            aria-label={`Play ${sound.kind}`}
            aria-pressed={playing === sound.kind}
            onClick={() => hear(sound)}
          >
            <Play aria-hidden size={14} />
            {lengthText(sound.length_s)}
          </Button>
        </li>
      ))}
    </ul>
  );
}
