// The TikTok shortlist on the Trends page (SPEC 13): TikTok gives no list of trending sounds, so this is
// yours. A sound's link pasted from TikTok, with its region, moods and energy for the suggestions; each row
// says when it was added, Stale after 14 days, and changes in place, opens in your browser and is removed
// only once asked. ClipForge keeps a sound's title and link, never the sound.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Pencil, Trash2 } from "lucide-react";
import { type FormEvent, type ReactNode, useEffect, useId, useState } from "react";

import { ApiError, api, key, sentence } from "../api.ts";
import { formatAgo } from "../lib/format.ts";
import { matches, NO_MATCH, type SoundFilters } from "../lib/trends.ts";
import type { TrendSound } from "../types.ts";
import { SELECT } from "./AddSource.tsx";
import { Badge } from "./Badge.tsx";
import { Button } from "./Button.tsx";
import { useConfirm } from "./Confirm.tsx";
import { Dialog, DialogFooter } from "./Dialog.tsx";
import { FieldError, TextField } from "./Field.tsx";
import { MOODS } from "./MusicList.tsx";
import { useToast } from "./Toasts.tsx";

export const NO_SHORTLIST = "Paste a tiktok.com/music link to start your shortlist.";
export const HEAD = "px-3 py-2 font-medium whitespace-nowrap";
export const CELL = "px-3 py-2.5 align-top";
export const NONE = <span className="text-faint">None</span>;
const CHECKBOX = "size-4 accent-accent-strong";
const LIMITS = { link: 500, title: 200 };

type Region = "" | "AU" | "US";
type Marks = { region: Region; moods: string[]; energy: number | null };
type Draft = Marks & { link: string; title: string };
type Problems = Partial<Record<"link" | "title" | "region" | "moods" | "energy" | "form", string>>;

const BLANK: Draft = { link: "", title: "", region: "", moods: [], energy: null };

// A section's quiet line when it has nothing to show.
export function Note({ children }: { children: ReactNode }) {
  return <p className="rounded-card border border-line bg-panel px-4 py-5 text-sm text-muted">{children}</p>;
}

export function SelectField({
  label,
  value,
  onChange,
  options,
  error,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: [string, string][];
  error?: string;
}) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-muted">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={error ? true : undefined}
        className={`${SELECT} self-start`}
      >
        {options.map(([v, text]) => (
          <option key={v} value={v}>
            {text}
          </option>
        ))}
      </select>
      {error && <FieldError text={error} />}
    </div>
  );
}

// Open, for a sound with a link: it opens in your default browser, by the id ClipForge stored it under.
export function OpenSound({ sound }: { sound: TrendSound }) {
  const toast = useToast();
  if (!sound.link_id) return null;
  const link = sound.link_id;
  async function open() {
    try {
      await api(`/api/links/${encodeURIComponent(link)}/open`, { method: "POST" });
      toast({ text: `Opened ${sound.title} in your browser.`, kind: "ok" });
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
    }
  }
  return (
    <Button variant="ghost" aria-label={`Open ${sound.title}`} onClick={() => void open()}>
      <ExternalLink aria-hidden size={14} />
      Open
    </Button>
  );
}

// The engine's field for a refusal, else the form's own line.
function refusal(err: unknown): Problems {
  const field = err instanceof ApiError ? err.field : null;
  const known = field === "link" || field === "title" || field === "region" || field === "moods" || field === "energy";
  return { [known ? field : "form"]: sentence(err) };
}

function body(marks: Marks) {
  return { region: marks.region || null, moods: marks.moods, energy: marks.energy };
}

function moodsText(moods: string[]): string {
  return moods.map((m) => MOODS[m] ?? m).join(", ");
}

export function Shortlist({ sounds, filters }: { sounds: TrendSound[]; filters: SoundFilters }) {
  const id = useId();
  const shown = sounds.filter((s) => matches(s, filters));
  const now = Date.now() / 1000;
  let rows;
  if (sounds.length === 0) rows = <Note>{NO_SHORTLIST}</Note>;
  else if (shown.length === 0) rows = <Note>{NO_MATCH}</Note>;
  else
    rows = (
      <div className="overflow-x-auto rounded-card border border-line bg-panel">
        <table aria-labelledby={id} className="w-full text-left text-sm">
          <thead className="border-b border-line text-xs text-muted">
            <tr>
              {["Sound", "Region", "Moods", "Energy", "Added"].map((label) => (
                <th key={label} scope="col" className={HEAD}>
                  {label}
                </th>
              ))}
              <th scope="col" className={HEAD}>
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {shown.map((sound) => (
              <SoundRow key={sound.id} sound={sound} now={now} />
            ))}
          </tbody>
        </table>
      </div>
    );
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <h2 id={id} className="text-base font-semibold">
        TikTok shortlist
      </h2>
      <p className="text-sm text-muted">
        TikTok gives no list of trending sounds, so this one is yours: paste a sound's link from TikTok to pick it for a
        post. You add it in TikTok's window when the post opens there. A sound added more than 14 days ago is marked
        Stale.
      </p>
      <AddSound />
      {rows}
    </section>
  );
}

function SoundRow({ sound, now }: { sound: TrendSound; now: number }) {
  const client = useQueryClient();
  const ask = useConfirm();
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const remove = useMutation({
    mutationFn: () => api<void>(`/api/trends/shortlist/${encodeURIComponent(sound.id)}`, { method: "DELETE" }),
    onSuccess: () => toast({ text: "Removed from your shortlist", kind: "ok", subject: sound.title }),
    onError: (err) => toast({ text: sentence(err), kind: "bad" }),
    onSettled: () => client.invalidateQueries({ queryKey: key.trends }),
  });

  async function removeIt() {
    const yes = await ask({
      title: "Remove this sound?",
      description: `${sound.title} comes off your shortlist. Posts that picked it keep it.`,
      action: "Remove sound",
      keep: "Keep it",
      danger: true,
    });
    if (yes) remove.mutate();
  }

  return (
    <tr className="border-b border-line last:border-0">
      <th scope="row" className={`${CELL} font-medium`} dir="auto">
        {sound.title}
      </th>
      <td className={CELL}>{sound.region ?? NONE}</td>
      <td className={CELL}>{sound.mood_tags.length ? moodsText(sound.mood_tags) : NONE}</td>
      <td className={`${CELL} tabular-nums`}>{sound.energy ?? NONE}</td>
      <td className={CELL}>
        <span className="flex flex-wrap items-center gap-2 whitespace-nowrap">
          {formatAgo(sound.created_at, now)}
          {sound.stale && <Badge tone="warn">Stale</Badge>}
        </span>
      </td>
      <td className={`${CELL} text-right`}>
        <div className="flex justify-end gap-1">
          <Button variant="ghost" onClick={() => setEditing(true)}>
            <Pencil aria-hidden size={14} />
            Edit
          </Button>
          <Button variant="ghost" onClick={() => void removeIt()} disabled={remove.isPending}>
            <Trash2 aria-hidden size={14} />
            Remove
          </Button>
          <OpenSound sound={sound} />
        </div>
        <EditSound sound={sound} open={editing} onClose={() => setEditing(false)} />
      </td>
    </tr>
  );
}

// A sound's region, moods and energy, as the add form and the Edit dialog ask for them.
function MarkFields({
  marks,
  onChange,
  problems,
}: {
  marks: Marks;
  onChange: (marks: Partial<Marks>) => void;
  problems: Problems;
}) {
  function toggleMood(mood: string, on: boolean) {
    const moods = on ? [...marks.moods, mood] : marks.moods.filter((m) => m !== mood);
    onChange({ moods: Object.keys(MOODS).filter((m) => moods.includes(m)) });
  }
  return (
    <>
      <fieldset className="flex flex-col gap-1">
        <legend className="text-xs font-medium text-muted">Moods</legend>
        <div className="flex flex-wrap gap-x-4 gap-y-1.5 pt-1">
          {Object.entries(MOODS).map(([mood, label]) => (
            <label key={mood} className="flex cursor-pointer items-center gap-2 text-sm">
              <input
                type="checkbox"
                className={CHECKBOX}
                checked={marks.moods.includes(mood)}
                onChange={(e) => toggleMood(mood, e.target.checked)}
              />
              {label}
            </label>
          ))}
        </div>
        {problems.moods && <FieldError text={problems.moods} />}
      </fieldset>
      <div className="flex flex-wrap gap-4">
        <SelectField
          label="Region"
          value={marks.region}
          onChange={(v) => onChange({ region: v as Region })}
          options={[
            ["", "No region"],
            ["AU", "AU"],
            ["US", "US"],
          ]}
          error={problems.region}
        />
        <SelectField
          label="Energy"
          value={marks.energy === null ? "" : String(marks.energy)}
          onChange={(v) => onChange({ energy: v ? Number(v) : null })}
          options={[
            ["", "Not set"],
            ["1", "1 (calm)"],
            ["2", "2"],
            ["3", "3"],
            ["4", "4"],
            ["5", "5 (all out)"],
          ]}
          error={problems.energy}
        />
      </div>
    </>
  );
}

function AddSound() {
  const client = useQueryClient();
  const toast = useToast();
  const [draft, setDraft] = useState<Draft>(BLANK);
  const [problems, setProblems] = useState<Problems>({});
  const [saving, setSaving] = useState(false);

  function edit(fields: Partial<Draft>) {
    setDraft((d) => ({ ...d, ...fields }));
    const cleared = Object.fromEntries(Object.keys(fields).map((f) => [f, undefined]));
    setProblems((p) => ({ ...p, ...cleared, form: undefined }));
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (saving) return;
    setSaving(true);
    try {
      const { sound } = await api<{ sound: TrendSound }>("/api/trends/shortlist", {
        method: "POST",
        body: JSON.stringify({ link: draft.link.trim(), title: draft.title.trim() || null, ...body(draft) }),
      });
      await client.invalidateQueries({ queryKey: key.trends });
      toast({ text: "Added to your shortlist", kind: "ok", subject: sound.title });
      setDraft(BLANK);
      setProblems({});
    } catch (err) {
      setProblems(refusal(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      aria-label="Add a sound"
      onSubmit={(e) => void submit(e)}
      noValidate
      className="flex flex-col gap-3 rounded-card border border-line bg-panel p-4"
    >
      <div className="grid gap-3 md:grid-cols-2">
        <TextField
          label="Sound link"
          placeholder="https://www.tiktok.com/music/..."
          value={draft.link}
          onChange={(v) => edit({ link: v })}
          error={problems.link}
          maxLength={LIMITS.link}
        />
        <TextField
          label="Title"
          hint="Optional. Left blank, it's the name in the link."
          value={draft.title}
          onChange={(v) => edit({ title: v })}
          error={problems.title}
          maxLength={LIMITS.title}
        />
      </div>
      <MarkFields marks={draft} onChange={edit} problems={problems} />
      {problems.form && <FieldError text={problems.form} />}
      <div>
        <Button type="submit" variant="primary" disabled={saving}>
          Add
        </Button>
      </div>
    </form>
  );
}

function EditSound({ sound, open, onClose }: { sound: TrendSound; open: boolean; onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [title, setTitle] = useState(sound.title);
  const [marks, setMarks] = useState<Marks>({ region: "", moods: [], energy: null });
  const [problems, setProblems] = useState<Problems>({});
  const [saving, setSaving] = useState(false);

  // Each time it opens, it starts from the sound as it is then; a refetch while it's open leaves it be.
  useEffect(() => {
    if (!open) return;
    setTitle(sound.title);
    setMarks({ region: sound.region ?? "", moods: sound.mood_tags, energy: sound.energy });
    setProblems({});
  }, [open]);

  function change(fields: Partial<Marks>) {
    setMarks((m) => ({ ...m, ...fields }));
    const cleared = Object.fromEntries(Object.keys(fields).map((f) => [f, undefined]));
    setProblems((p) => ({ ...p, ...cleared, form: undefined }));
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (saving) return;
    setSaving(true);
    try {
      const { sound: saved } = await api<{ sound: TrendSound }>(
        `/api/trends/shortlist/${encodeURIComponent(sound.id)}`,
        { method: "PATCH", body: JSON.stringify({ title: title.trim(), ...body(marks) }) },
      );
      await client.invalidateQueries({ queryKey: key.trends });
      toast({ text: "Saved", kind: "ok", subject: saved.title });
      onClose();
    } catch (err) {
      setProblems(refusal(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      title={`Edit ${sound.title}`}
      open={open}
      onOpenChange={(next) => {
        if (!next && !saving) onClose();
      }}
    >
      <form onSubmit={(e) => void submit(e)} noValidate className="flex flex-col gap-3 text-left">
        <TextField
          label="Title"
          value={title}
          onChange={(v) => {
            setTitle(v);
            setProblems((p) => ({ ...p, title: undefined, form: undefined }));
          }}
          error={problems.title}
          maxLength={LIMITS.title}
        />
        <MarkFields marks={marks} onChange={change} problems={problems} />
        {problems.form && <FieldError text={problems.form} />}
        <DialogFooter>
          <Button disabled={saving} onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={saving}>
            Save
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  );
}
