// The sound for one post in the Post dialog (SPEC 13): none, one that suits the clip, or any other on the
// account's list. Instagram adds a trending sound to the reel itself, at the level you set; a TikTok sound
// you add yourself in TikTok's window, so the post waits for you there. ClipForge never puts a platform's
// sound in the file.

import { useQuery } from "@tanstack/react-query";
import { useId } from "react";

import { api, key } from "../api.ts";
import { postSound, SOUND_LEVEL, soundLabel, soundValue } from "../lib/sounds.ts";
import type { Account, PlannedPost, PostSound, SoundSuggestion, Trends, TrendSound } from "../types.ts";
import { SELECT } from "./AddSource.tsx";
import { Badge } from "./Badge.tsx";

type Props = {
  post: PlannedPost;
  account: Account;
  route: "tiktok" | "instagram"; // pickable(account)
  sound: PostSound | null;
  open: boolean; // whether the post's fields show, so a closed one asks for nothing
  onPick: (sound: PostSound | null) => void;
};

const enc = encodeURIComponent;

export function SoundPicker({ post, account, route, sound, open, onPick }: Props) {
  const id = useId();
  const levelId = useId();
  const suggested = useQuery({
    queryKey: key.clipSounds(post.clip_id, post.platform, account.id),
    queryFn: ({ signal }) =>
      api<{ sounds: SoundSuggestion[] }>(
        `/api/clips/${enc(post.clip_id)}/sounds?platform=${post.platform}&account_id=${enc(account.id)}`,
        { signal },
      ),
    enabled: open,
  });
  const trends = useQuery({
    queryKey: key.trends,
    queryFn: ({ signal }) => api<Trends>("/api/trends", { signal }),
    enabled: open,
  });
  const suits = (suggested.data?.sounds ?? []).filter((s) => s.kind !== "music" && s.external_id);
  const seen = new Set(suits.map(soundValue));
  const list: TrendSound[] =
    route === "tiktok"
      ? (trends.data?.shortlist ?? [])
      : (trends.data?.instagram.find((b) => b.account.id === account.id)?.sounds ?? []);
  const rest = list.filter((s) => !seen.has(soundValue(s)));
  const picked = sound ? soundValue(sound) : "";
  const listed = picked === "" || seen.has(picked) || rest.some((s) => soundValue(s) === picked);
  const loaded = suggested.isSuccess && trends.isSuccess;

  function pick(value: string) {
    const found = [...suits, ...rest].find((s) => soundValue(s) === value);
    onPick(found ? postSound(found, sound?.volume ?? SOUND_LEVEL) : null);
  }

  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={id} className="text-xs font-medium text-muted">
        Sound
      </label>
      <select id={id} value={picked} onChange={(e) => pick(e.target.value)} className={SELECT}>
        <option value="">None</option>
        {suits.length > 0 && (
          <optgroup label="Suits this clip">
            {suits.map((s) => (
              <option key={soundValue(s)} value={soundValue(s)}>
                {soundLabel(s)}
              </option>
            ))}
          </optgroup>
        )}
        {rest.length > 0 && (
          <optgroup label={route === "tiktok" ? "The rest of your shortlist" : "The rest of its trending list"}>
            {rest.map((s) => (
              <option key={soundValue(s)} value={soundValue(s)}>
                {soundLabel(s)}
              </option>
            ))}
          </optgroup>
        )}
        {sound && !listed && <option value={picked}>{soundLabel(sound)}</option>}
      </select>
      {loaded && suits.length === 0 && rest.length === 0 && (
        <p className="text-xs text-faint">
          {route === "tiktok"
            ? "Your TikTok shortlist is empty. Paste sounds into it on the Trends page."
            : "No trending sounds read for this account yet. Check on the Trends page."}
        </p>
      )}
      {sound && route === "tiktok" && (
        <div className="flex flex-col items-start gap-1">
          <Badge tone="info">Assisted: you add the sound</Badge>
          <p className="text-xs text-faint">
            You add it in TikTok's window when the post opens there, then post it yourself.
          </p>
        </div>
      )}
      {sound && route === "instagram" && (
        <div className="flex flex-col gap-1">
          <label htmlFor={levelId} className="text-xs font-medium text-muted">
            Sound level
          </label>
          <input
            id={levelId}
            type="range"
            min={0}
            max={100}
            step={1}
            value={sound.volume}
            onChange={(e) => onPick({ ...sound, volume: Number(e.target.value) })}
            className="w-full accent-accent-strong"
          />
          <span className="text-xs text-faint tabular-nums">
            {`${sound.volume} of 100, with the clip's own sound at full`}
          </span>
        </div>
      )}
    </div>
  );
}
