// Your music library (SPEC 8.6 and 12): tracks you own or have a licence for, each with who made it, where
// it's from, what the licence lets you do, whether it asks for credit in the caption, and the moods and energy
// the suggestions match a clip with. Each plays here, changes in place, and is deleted only once asked, saying
// how many clips use it.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Music, Pause, Pencil, Play, Plus, Trash2 } from "lucide-react";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import { ApiError, api, key, sentence, upload } from "../api.ts";
import { isMusicFile, MUSIC_ACCEPT } from "../lib/drop.ts";
import { formatDuration } from "../lib/format.ts";
import type { LibraryItem } from "../types.ts";
import { SELECT } from "./AddSource.tsx";
import { Badge } from "./Badge.tsx";
import { Button } from "./Button.tsx";
import { useConfirm } from "./Confirm.tsx";
import { Dialog, DialogFooter } from "./Dialog.tsx";
import { Empty } from "./Empty.tsx";
import { FieldError, TextField } from "./Field.tsx";
import { useToast } from "./Toasts.tsx";

// The moods a track can have, as the engine names them (clipforge/brain/schemas.py Mood).
export const MOODS: Record<string, string> = {
  hype: "Hype",
  funny: "Funny",
  inspiring: "Inspiring",
  serious: "Serious",
  emotional: "Emotional",
  calm: "Calm",
  dark: "Dark",
  curious: "Curious",
};

// The engine's own sentences (clipforge/library/music.py), said here before a long upload starts.
export const NO_TRACK = "Choose an audio file first.";
export const NOT_AUDIO = "That file has no sound ClipForge can read. Pick an audio file such as MP3, M4A or WAV.";
export const TRACK_LICENCE = "Say what the licence lets you do.";
export const NO_TITLE = "Give the track a title.";
const LIMITS = { title: 120, artist: 200, source_url: 500, license: 200, license_url: 500 };
const CHECKBOX = "size-4 accent-accent-strong";

function usesText(n: number): string {
  if (n === 0) return "Not used yet";
  return n === 1 ? "Used in 1 clip" : `Used in ${n} clips`;
}

function webLink(url: string | null): string | null {
  return url && /^https?:\/\//i.test(url) ? url : null;
}

export function MusicList() {
  const items = useQuery({
    queryKey: key.music,
    queryFn: ({ signal }) => api<{ items: LibraryItem[] }>("/api/library?kind=music", { signal }),
  });
  const [adding, setAdding] = useState(false);
  // One track plays at a time.
  const audio = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);
  useEffect(() => () => audio.current?.pause(), []);

  function hear(item: LibraryItem) {
    const was = playing;
    audio.current?.pause();
    if (was === item.id) return; // pressed again: stopped
    const next = new Audio(item.url);
    audio.current = next;
    const done = () => {
      if (audio.current === next) setPlaying(null);
    };
    for (const name of ["ended", "pause", "error"]) next.addEventListener(name, done);
    setPlaying(item.id);
    void next.play().catch(done);
  }

  const add = (
    <Button variant="primary" onClick={() => setAdding(true)}>
      <Plus aria-hidden size={16} />
      Add music
    </Button>
  );
  let body;
  if (items.isPending) body = <p className="text-sm text-muted">Loading your music.</p>;
  else if (items.isError) body = <p className="text-sm text-bad">{sentence(items.error)}</p>;
  else if (items.data.items.length === 0)
    body = (
      <Empty icon={Music} title="No music yet." action={add}>
        <p>Add tracks you own or have a licence for.</p>
      </Empty>
    );
  else
    body = (
      <div className="flex flex-col gap-4">
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm text-muted">Tracks you own or have a licence for, played quietly under a clip.</p>
          {add}
        </div>
        <ul className="flex flex-col gap-2">
          {items.data.items.map((item) => (
            <TrackRow key={item.id} item={item} playing={playing === item.id} onPlay={() => hear(item)} />
          ))}
        </ul>
      </div>
    );
  return (
    <>
      {body}
      <TrackDialog open={adding} onClose={() => setAdding(false)} />
    </>
  );
}

function TrackRow({ item, playing, onPlay }: { item: LibraryItem; playing: boolean; onPlay: () => void }) {
  const client = useQueryClient();
  const ask = useConfirm();
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const remove = useMutation({
    mutationFn: () => api<void>(`/api/library/${encodeURIComponent(item.id)}`, { method: "DELETE" }),
    onSuccess: () => toast({ text: "Deleted from the library", kind: "ok", subject: item.name }),
    onError: (err) => toast({ text: sentence(err), kind: "bad" }),
    onSettled: () => client.invalidateQueries({ queryKey: key.music }),
  });

  async function deleteIt() {
    let description = `${item.name} is removed from this Mac.`;
    if (item.uses === 1) description = "1 clip uses this track. It will have no music the next time it renders.";
    else if (item.uses > 1)
      description = `${item.uses} clips use this track. They will have no music the next time they render.`;
    const yes = await ask({ title: "Delete this track?", description, action: "Delete track", keep: "Keep it", danger: true });
    if (yes) remove.mutate();
  }

  const licence = webLink(item.license_url);
  const source = webLink(item.source_url);
  const facts = [
    item.duration_s !== null && formatDuration(item.duration_s),
    item.energy !== null && `Energy ${item.energy}`,
    item.moods.map((mood) => MOODS[mood] ?? mood).join(", "),
  ].filter(Boolean);
  return (
    <li
      aria-label={item.name}
      className="flex flex-wrap items-center gap-3 rounded-card border border-line bg-panel px-4 py-3 text-sm"
    >
      <Button variant="ghost" aria-label={`Play ${item.name}`} aria-pressed={playing} onClick={onPlay}>
        {playing ? <Pause aria-hidden size={16} /> : <Play aria-hidden size={16} />}
      </Button>
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="font-medium" dir="auto">
            {item.name}
          </h3>
          {item.credit_required && <Badge tone="info">Credit in captions</Badge>}
        </div>
        {item.credit && (
          <p className="text-muted" dir="auto">
            {item.credit}
          </p>
        )}
        <p className="text-xs text-muted tabular-nums">{facts.join(" · ")}</p>
        {item.license && (
          <p className="text-xs text-muted">
            {"Licence: "}
            {licence ? (
              <a href={licence} target="_blank" rel="noreferrer" className="text-fg underline underline-offset-2" dir="auto">
                {item.license}
              </a>
            ) : (
              <span dir="auto">{item.license}</span>
            )}
            {source && (
              <>
                {" · "}
                <a href={source} target="_blank" rel="noreferrer" className="text-fg underline underline-offset-2">
                  Where it's from
                </a>
              </>
            )}
          </p>
        )}
      </div>
      <span className="text-xs text-faint">{usesText(item.uses)}</span>
      <div className="flex items-center gap-1">
        <Button variant="ghost" onClick={() => setEditing(true)}>
          <Pencil aria-hidden size={14} />
          Edit
        </Button>
        <Button variant="ghost" onClick={() => void deleteIt()} disabled={remove.isPending}>
          <Trash2 aria-hidden size={14} />
          Delete
        </Button>
      </div>
      <TrackDialog item={item} open={editing} onClose={() => setEditing(false)} />
    </li>
  );
}

type Draft = {
  title: string;
  artist: string;
  source_url: string;
  license: string;
  license_url: string;
  credit_required: boolean;
  moods: string[];
  energy: number | null;
};
type Problems = Partial<Record<keyof Draft | "file" | "form", string>>;

const BLANK: Draft = {
  title: "",
  artist: "",
  source_url: "",
  license: "",
  license_url: "",
  credit_required: false,
  moods: [],
  energy: null,
};

function draftOf(item: LibraryItem): Draft {
  return {
    title: item.name,
    artist: item.credit ?? "",
    source_url: item.source_url ?? "",
    license: item.license ?? "",
    license_url: item.license_url ?? "",
    credit_required: item.credit_required,
    moods: item.moods,
    energy: item.energy,
  };
}

function stem(name: string): string {
  return name.replace(/\.[^.]+$/, "");
}

// What has to be there before the file goes (or the change is sent), in the order the form asks for it.
function missing(adding: boolean, file: File | null, draft: Draft): Problems {
  const found: Problems = {};
  if (adding && !file) found.file = NO_TRACK;
  if (!adding && !draft.title.trim()) found.title = NO_TITLE;
  if (!draft.license.trim()) found.license = TRACK_LICENCE;
  return found;
}

// Adds a track, or with ``item`` changes that one; its file stays as it is.
function TrackDialog({ item, open, onClose }: { item?: LibraryItem; open: boolean; onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const adding = item === undefined;
  const [file, setFile] = useState<File | null>(null);
  const [draft, setDraft] = useState<Draft>(BLANK);
  const [problems, setProblems] = useState<Problems>({});
  const [progress, setProgress] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [refused, setRefused] = useState(0);
  const picker = useRef<HTMLInputElement>(null);
  const form = useRef<HTMLFormElement>(null);
  const energyId = useId();
  const busy = progress !== null || saving;

  // Each time it opens, it starts from the track as it is then; a refetch while it's open leaves it be.
  useEffect(() => {
    if (open) setDraft(item ? draftOf(item) : BLANK);
  }, [open]);

  // After a refusal, the focus goes to the first thing to fix.
  useEffect(() => {
    if (refused) form.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
  }, [refused]);

  function reset() {
    setFile(null);
    setDraft(BLANK);
    setProblems({});
    setProgress(null);
  }

  function close() {
    reset();
    onClose();
  }

  function edit<K extends keyof Draft>(field: K, value: Draft[K]) {
    setDraft((d) => ({ ...d, [field]: value }));
    setProblems((p) => ({ ...p, [field]: undefined, form: undefined }));
  }

  function toggleMood(mood: string, on: boolean) {
    const moods = on ? [...draft.moods, mood] : draft.moods.filter((m) => m !== mood);
    edit("moods", Object.keys(MOODS).filter((m) => moods.includes(m)));
  }

  function choose(chosen: File) {
    if (!isMusicFile(chosen.name)) {
      setProblems((p) => ({ ...p, file: NOT_AUDIO }));
      return;
    }
    // The title follows the file until someone types their own.
    setDraft((d) => (d.title === "" || (file && d.title === stem(file.name)) ? { ...d, title: stem(chosen.name) } : d));
    setFile(chosen);
    setProblems((p) => ({ ...p, file: undefined, title: undefined, form: undefined }));
  }

  function refuse(err: unknown) {
    const field = err instanceof ApiError ? err.field : null;
    const known = field && (field in LIMITS || field === "moods" || field === "energy" || field === "file");
    setProblems({ [known ? (field as keyof Problems) : "form"]: sentence(err) });
    setRefused((n) => n + 1);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    const found = missing(adding, file, draft);
    if (Object.keys(found).length) {
      setProblems(found);
      setRefused((n) => n + 1);
      return;
    }
    const fields = { ...draft, title: draft.title.trim() };
    setProblems({});
    try {
      let saved: LibraryItem;
      if (file) {
        setProgress(0);
        saved = await upload<LibraryItem>(
          "/api/library",
          file,
          {
            "X-File-Name": encodeURIComponent(file.name),
            "X-Item": encodeURIComponent(JSON.stringify({ kind: "music", ...fields, title: fields.title || stem(file.name) })),
          },
          setProgress,
        );
      } else {
        setSaving(true);
        saved = await api<LibraryItem>(`/api/library/${encodeURIComponent(item!.id)}`, {
          method: "PATCH",
          body: JSON.stringify(fields),
        });
      }
      await client.invalidateQueries({ queryKey: key.music });
      toast({ text: adding ? "Added to the library" : "Saved", kind: "ok", subject: saved.name });
      close();
    } catch (err) {
      setProgress(null);
      refuse(err);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      title={adding ? "Add music" : `Edit ${item.name}`}
      description={
        adding ? "A track you own or have a licence for. ClipForge keeps a copy and plays it quietly under clips." : undefined
      }
      open={open}
      onOpenChange={(next) => {
        if (next || busy) return; // an upload on its way finishes first
        close();
      }}
    >
      <form ref={form} onSubmit={(e) => void submit(e)} noValidate className="flex flex-col gap-3">
        {adding && (
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted">File</span>
            <div className="flex items-center gap-3">
              <Button onClick={() => picker.current?.click()} aria-invalid={problems.file ? true : undefined} disabled={busy}>
                Choose file
              </Button>
              <span className="min-w-0 truncate text-sm" dir="auto">
                {file ? file.name : "No file chosen"}
              </span>
            </div>
            <input
              ref={picker}
              type="file"
              accept={MUSIC_ACCEPT}
              tabIndex={-1}
              className="hidden"
              onChange={(e) => {
                const chosen = e.target.files?.[0];
                e.target.value = ""; // so choosing the same file again still counts
                if (chosen) choose(chosen);
              }}
            />
            {problems.file && <FieldError text={problems.file} />}
          </div>
        )}
        <TextField label="Title" value={draft.title} onChange={(v) => edit("title", v)} error={problems.title} maxLength={LIMITS.title} />
        <TextField
          label="Artist"
          hint="Who made the track, as a caption credit names them."
          value={draft.artist}
          onChange={(v) => edit("artist", v)}
          error={problems.artist}
          maxLength={LIMITS.artist}
        />
        <TextField
          label="Where it's from"
          hint="Optional. A link to the track's page."
          value={draft.source_url}
          onChange={(v) => edit("source_url", v)}
          error={problems.source_url}
          maxLength={LIMITS.source_url}
        />
        <TextField
          label="Licence"
          hint="What the licence lets you do, such as 'Bought from Artlist for social use'."
          value={draft.license}
          onChange={(v) => edit("license", v)}
          error={problems.license}
          maxLength={LIMITS.license}
        />
        <TextField
          label="Licence link"
          hint="Optional. Where the licence is."
          value={draft.license_url}
          onChange={(v) => edit("license_url", v)}
          error={problems.license_url}
          maxLength={LIMITS.license_url}
        />
        <label className="flex cursor-pointer items-center gap-2 text-sm">
          <input
            type="checkbox"
            className={CHECKBOX}
            checked={draft.credit_required}
            onChange={(e) => edit("credit_required", e.target.checked)}
          />
          The licence asks for credit
        </label>
        <fieldset className="flex flex-col gap-1">
          <legend className="text-xs font-medium text-muted">Moods</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1.5 pt-1">
            {Object.entries(MOODS).map(([mood, label]) => (
              <label key={mood} className="flex cursor-pointer items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  className={CHECKBOX}
                  checked={draft.moods.includes(mood)}
                  onChange={(e) => toggleMood(mood, e.target.checked)}
                />
                {label}
              </label>
            ))}
          </div>
          {problems.moods && <FieldError text={problems.moods} />}
        </fieldset>
        <div className="flex flex-col gap-1">
          <label htmlFor={energyId} className="text-xs font-medium text-muted">
            Energy
          </label>
          <select
            id={energyId}
            value={draft.energy ?? ""}
            onChange={(e) => edit("energy", e.target.value ? Number(e.target.value) : null)}
            aria-invalid={problems.energy ? true : undefined}
            className={`${SELECT} self-start`}
          >
            <option value="">Not set</option>
            <option value="1">1 (calm)</option>
            <option value="2">2</option>
            <option value="3">3</option>
            <option value="4">4</option>
            <option value="5">5 (all out)</option>
          </select>
          {problems.energy && <FieldError text={problems.energy} />}
        </div>
        {problems.form && <FieldError text={problems.form} />}
        {progress !== null && file && (
          <div
            role="progressbar"
            aria-label={`Uploading ${file.name}`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(progress * 100)}
            className="h-1.5 overflow-hidden rounded-full bg-panel-2"
          >
            <div className="h-full bg-accent transition-[width]" style={{ width: `${Math.round(progress * 100)}%` }} />
          </div>
        )}
        <DialogFooter>
          <Button disabled={busy} onClick={close}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={busy}>
            {progress !== null ? `Uploading ${Math.round(progress * 100)}%` : adding ? "Add" : "Save"}
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  );
}
