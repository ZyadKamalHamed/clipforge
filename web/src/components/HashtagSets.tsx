// Hashtag sets on the Trends page (SPEC 13): the hashtags you use for a niche, named, ready to copy into a
// caption in one go. A set is made with its niche, name and tags, copied, changed in place and deleted only
// once asked. The niches are the ones in Settings.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Pencil, Trash2 } from "lucide-react";
import { type FormEvent, useEffect, useId, useState } from "react";

import { ApiError, api, key, sentence } from "../api.ts";
import { splitTags, tagLine } from "../lib/trends.ts";
import { settingsQuery } from "../state.ts";
import type { HashtagSet } from "../types.ts";
import { Button } from "./Button.tsx";
import { useConfirm } from "./Confirm.tsx";
import { Dialog, DialogFooter } from "./Dialog.tsx";
import { FieldError, TextField } from "./Field.tsx";
import { Note, SelectField } from "./Shortlist.tsx";
import { useToast } from "./Toasts.tsx";

export const NO_SETS = "No hashtag sets yet.";
const LIMITS = { name: 60, tags: 2000 };

type Draft = { niche: string; name: string; tags: string };
type Problems = Partial<Record<keyof Draft | "form", string>>;

const BLANK: Draft = { niche: "", name: "", tags: "" };

function hashtagsText(n: number): string {
  return n === 1 ? "1 hashtag" : `${n} hashtags`;
}

function refusal(err: unknown): Problems {
  const field = err instanceof ApiError ? err.field : null;
  const known = field === "niche" || field === "name" || field === "tags";
  return { [known ? field : "form"]: sentence(err) };
}

function body(draft: Draft) {
  return { niche: draft.niche, name: draft.name.trim(), tags: splitTags(draft.tags) };
}

export function HashtagSets({ sets }: { sets: HashtagSet[] }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <h2 id={id} className="text-base font-semibold">
        Hashtag sets
      </h2>
      <p className="text-sm text-muted">The hashtags you use for a niche, ready to copy into a caption.</p>
      <SetForm />
      {sets.length === 0 ? (
        <Note>{NO_SETS}</Note>
      ) : (
        <ul aria-labelledby={id} className="grid gap-3 md:grid-cols-2">
          {sets.map((set) => (
            <SetItem key={set.id} set={set} />
          ))}
        </ul>
      )}
    </section>
  );
}

// The niche, name and hashtags, as the new set's form and the Edit dialog ask for them.
function SetFields({
  draft,
  onChange,
  problems,
}: {
  draft: Draft;
  onChange: (fields: Partial<Draft>) => void;
  problems: Problems;
}) {
  const niches = useQuery(settingsQuery).data?.values.briefs.niches.map((n) => n.name) ?? [];
  // A set keeps its niche when Settings no longer has it, so it still shows as picked.
  const names = draft.niche && !niches.includes(draft.niche) ? [draft.niche, ...niches] : niches;
  return (
    <>
      <div className="flex flex-wrap gap-3">
        <SelectField
          label="Niche"
          value={draft.niche}
          onChange={(v) => onChange({ niche: v })}
          options={[["", "Pick a niche"], ...names.map((n): [string, string] => [n, n])]}
          error={problems.niche}
        />
        <div className="min-w-48 flex-1">
          <TextField
            label="Name"
            value={draft.name}
            onChange={(v) => onChange({ name: v })}
            error={problems.name}
            maxLength={LIMITS.name}
          />
        </div>
      </div>
      <TextField
        label="Hashtags"
        hint="Separate them with spaces or commas, with or without #."
        value={draft.tags}
        onChange={(v) => onChange({ tags: v })}
        error={problems.tags}
        maxLength={LIMITS.tags}
      />
    </>
  );
}

function SetForm() {
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
      const { set } = await api<{ set: HashtagSet }>("/api/trends/sets", {
        method: "POST",
        body: JSON.stringify(body(draft)),
      });
      await client.invalidateQueries({ queryKey: key.trends });
      toast({ text: "Added the set", kind: "ok", subject: set.name });
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
      aria-label="New hashtag set"
      onSubmit={(e) => void submit(e)}
      noValidate
      className="flex flex-col gap-3 rounded-card border border-line bg-panel p-4"
    >
      <SetFields draft={draft} onChange={edit} problems={problems} />
      {problems.form && <FieldError text={problems.form} />}
      <div>
        <Button type="submit" variant="primary" disabled={saving}>
          Add set
        </Button>
      </div>
    </form>
  );
}

function SetItem({ set }: { set: HashtagSet }) {
  const client = useQueryClient();
  const ask = useConfirm();
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const remove = useMutation({
    mutationFn: () => api<void>(`/api/trends/sets/${encodeURIComponent(set.id)}`, { method: "DELETE" }),
    onSuccess: () => toast({ text: "Deleted the set", kind: "ok", subject: set.name }),
    onError: (err) => toast({ text: sentence(err), kind: "bad" }),
    onSettled: () => client.invalidateQueries({ queryKey: key.trends }),
  });

  async function copy() {
    try {
      await navigator.clipboard.writeText(tagLine(set.tags));
      toast({ text: `Copied ${hashtagsText(set.tags.length)}`, kind: "ok", subject: set.name });
    } catch {
      toast({ text: "The hashtags couldn't be copied. Select them and copy them yourself.", kind: "bad" });
    }
  }

  async function deleteIt() {
    const yes = await ask({
      title: "Delete this set?",
      description: `${set.name} and its ${hashtagsText(set.tags.length)} are deleted. Captions that have them keep them.`,
      action: "Delete set",
      keep: "Keep it",
      danger: true,
    });
    if (yes) remove.mutate();
  }

  return (
    <li aria-label={set.name} className="flex flex-col gap-2 rounded-card border border-line bg-panel p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h3 className="text-sm font-semibold" dir="auto">
            {set.name}
          </h3>
          <p className="text-xs text-muted" dir="auto">
            {set.niche}
          </p>
        </div>
        <span className="shrink-0 text-xs text-muted">{hashtagsText(set.tags.length)}</span>
      </div>
      <p className="text-sm break-words select-text" dir="auto">
        {tagLine(set.tags)}
      </p>
      <div className="flex flex-wrap gap-1">
        <Button variant="ghost" onClick={() => void copy()}>
          <Copy aria-hidden size={14} />
          Copy
        </Button>
        <Button variant="ghost" onClick={() => setEditing(true)}>
          <Pencil aria-hidden size={14} />
          Edit
        </Button>
        <Button variant="ghost" onClick={() => void deleteIt()} disabled={remove.isPending}>
          <Trash2 aria-hidden size={14} />
          Delete
        </Button>
      </div>
      <EditSet set={set} open={editing} onClose={() => setEditing(false)} />
    </li>
  );
}

function EditSet({ set, open, onClose }: { set: HashtagSet; open: boolean; onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [draft, setDraft] = useState<Draft>(BLANK);
  const [problems, setProblems] = useState<Problems>({});
  const [saving, setSaving] = useState(false);

  // Each time it opens, it starts from the set as it is then; a refetch while it's open leaves it be.
  useEffect(() => {
    if (!open) return;
    setDraft({ niche: set.niche, name: set.name, tags: tagLine(set.tags) });
    setProblems({});
  }, [open]);

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
      const { set: saved } = await api<{ set: HashtagSet }>(`/api/trends/sets/${encodeURIComponent(set.id)}`, {
        method: "PATCH",
        body: JSON.stringify(body(draft)),
      });
      await client.invalidateQueries({ queryKey: key.trends });
      toast({ text: "Saved", kind: "ok", subject: saved.name });
      onClose();
    } catch (err) {
      setProblems(refusal(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      title={`Edit ${set.name}`}
      open={open}
      onOpenChange={(next) => {
        if (!next && !saving) onClose();
      }}
    >
      <form onSubmit={(e) => void submit(e)} noValidate className="flex flex-col gap-3">
        <SetFields draft={draft} onChange={edit} problems={problems} />
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
