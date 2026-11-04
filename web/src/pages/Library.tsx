// The Library page (SPEC 12 and 14.7): gameplay videos added with who made them and the licence that lets
// clips use them, under the starter list of videos whose uploaders say they're free to use (each one
// downloads when you press Download, not before), your music (components/MusicList.tsx), the sound effects
// to hear, and the caption fonts drawn in their own face with their licences.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, ExternalLink, Gamepad2, Plus, Trash2 } from "lucide-react";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import { ApiError, api, key, sentence, upload } from "../api.ts";
import { Button } from "../components/Button.tsx";
import { useConfirm } from "../components/Confirm.tsx";
import { Dialog, DialogFooter } from "../components/Dialog.tsx";
import { Empty } from "../components/Empty.tsx";
import { FieldError, TextField } from "../components/Field.tsx";
import { MusicList } from "../components/MusicList.tsx";
import { SoundList } from "../components/Sounds.tsx";
import { Tabs } from "../components/Tabs.tsx";
import { useToast } from "../components/Toasts.tsx";
import { isVideoFile, NOT_A_VIDEO, VIDEO_ACCEPT } from "../lib/drop.ts";
import { formatBytes, formatDuration } from "../lib/format.ts";
import type { Font, LibraryItem, StarterEntry, StarterList } from "../types.ts";

type Tab = "gameplay" | "music" | "sfx" | "fonts";

// The engine's own sentences (clipforge/server/routes/library.py), said here before a long upload starts.
export const NO_CREDIT = "Say who made this video, so clips can credit them.";
export const NO_LICENCE =
  "Say which licence lets you use this video, like CC BY 4.0 or your own footage.";
export const NO_FILE = "Choose a video file first.";
export const FIRST_PART = "Downloads its first 20 minutes."; // a video longer than that
const LIMITS = {
  name: 120,
  credit: 200,
  license: 200,
  license_url: 500,
  notes: 1000,
};

export function Library() {
  const [tab, setTab] = useState<Tab>("gameplay");
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5">
      <h1 className="text-xl font-semibold">Library</h1>
      <Tabs
        label="Library"
        value={tab}
        onChange={setTab}
        tabs={[
          { value: "gameplay", label: "Gameplay", content: () => <Gameplay /> },
          { value: "music", label: "Music", content: () => <MusicList /> },
          { value: "sfx", label: "SFX", content: () => <Effects /> },
          { value: "fonts", label: "Fonts", content: () => <Fonts /> },
        ]}
      />
    </div>
  );
}

// Gameplay

function Gameplay() {
  const items = useQuery({
    queryKey: key.gameplay,
    queryFn: ({ signal }) =>
      api<{ items: LibraryItem[] }>("/api/library?kind=gameplay", { signal }),
  });
  const [adding, setAdding] = useState(false);
  const add = (
    <Button variant="primary" onClick={() => setAdding(true)}>
      <Plus aria-hidden size={16} />
      Add gameplay
    </Button>
  );

  let body;
  if (items.isPending)
    body = <p className="text-sm text-muted">Loading the library.</p>;
  else if (items.isError)
    body = <p className="text-sm text-bad">{sentence(items.error)}</p>;
  else if (items.data.items.length === 0)
    body = (
      <Empty icon={Gamepad2} title="No gameplay yet." action={add}>
        <p>
          Gameplay plays muted, under the speaker, in clips with the gameplay
          layout.
        </p>
        <p>Add videos you made or have a licence to use, with who made them.</p>
      </Empty>
    );
  else
    body = (
      <div className="flex flex-col gap-4">
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm text-muted">
            Gameplay plays muted, under the speaker, in clips with the gameplay
            layout.
          </p>
          {add}
        </div>
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-4">
          {items.data.items.map((item) => (
            <li key={item.id}>
              <GameplayCard item={item} />
            </li>
          ))}
        </ul>
      </div>
    );
  return (
    <div className="flex flex-col gap-8">
      <Starter />
      <section aria-label="Your gameplay">{body}</section>
      <AddGameplay open={adding} onClose={() => setAdding(false)} />
    </div>
  );
}

// The starter list (clipforge/library/starter.py): nothing downloads until you press Download, and the
// download's job moves its row on (events.ts refreshes the list and, once it's done, your gameplay).
function Starter() {
  const id = useId();
  const list = useQuery({
    queryKey: key.starter,
    queryFn: ({ signal }) =>
      api<StarterList>("/api/library/starter", { signal }),
  });
  let body;
  if (list.isPending)
    body = <p className="text-sm text-muted">Loading the starter list.</p>;
  else if (list.isError)
    body = <p className="text-sm text-bad">{sentence(list.error)}</p>;
  else
    body = (
      <>
        <p className="text-sm text-muted">{list.data.note}</p>
        <ul className="flex flex-col divide-y divide-line rounded-card border border-line bg-panel">
          {list.data.entries.map((entry) => (
            <StarterRow key={entry.id} entry={entry} />
          ))}
        </ul>
      </>
    );
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <h2 id={id} className="font-medium">
        Starter list
      </h2>
      {body}
    </section>
  );
}

function StarterRow({ entry }: { entry: StarterEntry }) {
  const client = useQueryClient();
  const toast = useToast();
  const download = useMutation({
    mutationFn: () =>
      api<StarterEntry>(
        `/api/library/starter/${encodeURIComponent(entry.id)}/download`,
        { method: "POST" },
      ),
    onSuccess: (row) =>
      client.setQueryData<StarterList>(key.starter, (old) =>
        old
          ? {
              ...old,
              entries: old.entries.map((e) => (e.id === row.id ? row : e)),
            }
          : old,
      ),
    onError: (err) => toast({ text: sentence(err), kind: "bad" }),
  });

  async function open() {
    try {
      await api(
        `/api/links/${encodeURIComponent(`starter:${entry.id}`)}/open`,
        { method: "POST" },
      );
      toast({ text: `Opened ${entry.title} in your browser.`, kind: "ok" });
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
    }
  }

  const facts = [
    entry.uploader,
    formatDuration(entry.duration_s),
    entry.size.replace("x", "×"),
  ];
  let action;
  if (entry.state === "added")
    action = <span className="text-xs font-medium text-ok">Added</span>;
  else if (entry.state === "downloading" || download.isPending)
    action = <span className="text-xs text-muted">Downloading.</span>;
  else
    action = (
      <Button
        variant="primary"
        aria-label={`Download ${entry.title}`}
        onClick={() => download.mutate()}
      >
        <Download aria-hidden size={14} />
        {entry.state === "failed" ? "Download again" : "Download"}
      </Button>
    );
  return (
    <li
      aria-label={entry.title}
      className="flex flex-wrap items-start justify-between gap-3 p-3 text-sm"
    >
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <p className="text-xs text-muted">{entry.game}</p>
        <h3 className="font-medium" dir="auto">
          {entry.title}
        </h3>
        <p className="text-xs text-muted tabular-nums">{facts.join(" · ")}</p>
        <p className="text-muted">Licence: {entry.licence}</p>
        {entry.chips.length > 0 && (
          <ul aria-label="Limits" className="flex flex-wrap gap-2">
            {entry.chips.map((chip) => (
              <li
                key={chip}
                className="rounded-full border border-line px-2.5 py-0.5 text-xs text-warn"
              >
                {chip}
              </li>
            ))}
          </ul>
        )}
        <p className="text-xs text-faint">{entry.terms}</p>
        {entry.first_part && <p className="text-xs text-muted">{FIRST_PART}</p>}
        {entry.state === "failed" && entry.error && (
          <FieldError text={entry.error} />
        )}
      </div>
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          aria-label={`Open ${entry.title}`}
          onClick={() => void open()}
        >
          <ExternalLink aria-hidden size={14} />
          Open
        </Button>
        {action}
      </div>
    </li>
  );
}

function usesText(n: number): string {
  if (n === 0) return "Not used yet";
  return n === 1 ? "Used in 1 clip" : `Used in ${n} clips`;
}

function GameplayCard({ item }: { item: LibraryItem }) {
  const client = useQueryClient();
  const ask = useConfirm();
  const toast = useToast();
  const remove = useMutation({
    mutationFn: () =>
      api<void>(`/api/library/${encodeURIComponent(item.id)}`, {
        method: "DELETE",
      }),
    onSuccess: () =>
      toast({
        text: "Deleted from the library",
        kind: "ok",
        subject: item.name,
      }),
    onError: (err) => toast({ text: sentence(err), kind: "bad" }),
    onSettled: () => client.invalidateQueries({ queryKey: key.gameplay }),
  });

  async function deleteIt() {
    let after = "";
    if (item.uses === 1)
      after = " The clip that uses it picks other gameplay at its next render.";
    else if (item.uses > 1)
      after = ` The ${item.uses} clips that use it pick other gameplay at their next render.`;
    const yes = await ask({
      title: "Delete this gameplay?",
      description: `${item.name} is removed from this Mac.${after}`,
      action: "Delete",
      danger: true,
    });
    if (yes) remove.mutate();
  }

  const facts = [
    item.duration_s !== null && formatDuration(item.duration_s),
    item.size_bytes !== null && formatBytes(item.size_bytes),
    item.width && item.height && `${item.width}×${item.height}`,
  ].filter(Boolean);
  const link =
    item.license_url && /^https?:\/\//i.test(item.license_url)
      ? item.license_url
      : null;
  return (
    <article
      aria-label={item.name}
      className="flex flex-col overflow-hidden rounded-card border border-line bg-panel"
    >
      <video
        src={item.url}
        muted
        loop
        playsInline
        preload="metadata"
        aria-hidden
        onMouseEnter={(e) => void e.currentTarget.play().catch(() => undefined)}
        onMouseLeave={(e) => e.currentTarget.pause()}
        className="aspect-video w-full bg-black object-cover"
      />
      <div className="flex flex-1 flex-col gap-1 p-3 text-sm">
        <h3 className="font-medium" dir="auto">
          {item.name}
        </h3>
        <p className="text-xs text-muted tabular-nums">{facts.join(" · ")}</p>
        {item.credit && (
          <p className="text-muted">
            By <span dir="auto">{item.credit}</span>
          </p>
        )}
        {item.license && (
          <p className="text-muted">
            {"Licence: "}
            {link ? (
              <a
                href={link}
                target="_blank"
                rel="noreferrer"
                className="text-fg underline underline-offset-2"
                dir="auto"
              >
                {item.license}
              </a>
            ) : (
              <span dir="auto">{item.license}</span>
            )}
          </p>
        )}
        {item.notes && (
          <p className="text-xs text-faint" dir="auto">
            {item.notes}
          </p>
        )}
        <div className="mt-auto flex items-center justify-between gap-2 pt-2">
          <span className="text-xs text-faint">{usesText(item.uses)}</span>
          <Button
            variant="ghost"
            onClick={() => void deleteIt()}
            disabled={remove.isPending}
          >
            <Trash2 aria-hidden size={14} />
            Delete
          </Button>
        </div>
      </div>
    </article>
  );
}

type Draft = {
  name: string;
  credit: string;
  license: string;
  license_url: string;
  notes: string;
};
type Problems = Partial<Record<keyof Draft | "file" | "form", string>>;

const BLANK: Draft = {
  name: "",
  credit: "",
  license: "",
  license_url: "",
  notes: "",
};

function stem(name: string): string {
  return name.replace(/\.[^.]+$/, "");
}

// What has to be there before the file goes, in the order the form asks for it.
function missing(file: File | null, draft: Draft): Problems {
  const found: Problems = {};
  if (!file) found.file = NO_FILE;
  if (!draft.credit.trim()) found.credit = NO_CREDIT;
  if (!draft.license.trim()) found.license = NO_LICENCE;
  return found;
}

function AddGameplay({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [draft, setDraft] = useState<Draft>(BLANK);
  const [problems, setProblems] = useState<Problems>({});
  const [progress, setProgress] = useState<number | null>(null);
  const [refused, setRefused] = useState(0);
  const picker = useRef<HTMLInputElement>(null);
  const form = useRef<HTMLFormElement>(null);
  const busy = progress !== null;

  // After a refusal, the focus goes to the first thing to fix.
  useEffect(() => {
    if (refused)
      form.current
        ?.querySelector<HTMLElement>('[aria-invalid="true"]')
        ?.focus();
  }, [refused]);

  function reset() {
    setFile(null);
    setDraft(BLANK);
    setProblems({});
    setProgress(null);
  }

  function edit(field: keyof Draft, value: string) {
    setDraft((d) => ({ ...d, [field]: value }));
    setProblems((p) => ({ ...p, [field]: undefined, form: undefined }));
  }

  function choose(chosen: File) {
    if (!isVideoFile(chosen.name)) {
      setProblems((p) => ({ ...p, file: NOT_A_VIDEO }));
      return;
    }
    // The name follows the file until someone types their own.
    setDraft((d) =>
      d.name === "" || (file && d.name === stem(file.name))
        ? { ...d, name: stem(chosen.name) }
        : d,
    );
    setFile(chosen);
    setProblems((p) => ({
      ...p,
      file: undefined,
      name: undefined,
      form: undefined,
    }));
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    const found = missing(file, draft);
    if (!file || Object.keys(found).length) {
      setProblems(found);
      setRefused((n) => n + 1);
      return;
    }
    const item = {
      kind: "gameplay",
      name: draft.name.trim() || stem(file.name),
      credit: draft.credit,
      license: draft.license,
      license_url: draft.license_url.trim() || null,
      notes: draft.notes,
    };
    setProblems({});
    setProgress(0);
    try {
      const added = await upload<LibraryItem>(
        "/api/library",
        file,
        {
          "X-File-Name": encodeURIComponent(file.name),
          "X-Item": encodeURIComponent(JSON.stringify(item)),
        },
        setProgress,
      );
      await client.invalidateQueries({ queryKey: key.gameplay });
      toast({ text: "Added to the library", kind: "ok", subject: added.name });
      reset();
      onClose();
    } catch (err) {
      setProgress(null);
      const field = err instanceof ApiError ? err.field : null;
      const where =
        field && field in LIMITS
          ? (field as keyof Draft)
          : field === "file"
            ? "file"
            : "form";
      setProblems({ [where]: sentence(err) });
      setRefused((n) => n + 1);
    }
  }

  return (
    <Dialog
      title="Add gameplay"
      description="A video to play muted under the speaker. Clips credit whoever made it."
      open={open}
      onOpenChange={(next) => {
        if (next || busy) return; // an upload on its way finishes first
        reset();
        onClose();
      }}
    >
      <form
        ref={form}
        onSubmit={(e) => void submit(e)}
        noValidate
        className="flex flex-col gap-3"
      >
        <div className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted">Video file</span>
          <div className="flex items-center gap-3">
            <Button
              onClick={() => picker.current?.click()}
              aria-invalid={problems.file ? true : undefined}
              disabled={busy}
            >
              Choose video
            </Button>
            <span className="min-w-0 truncate text-sm" dir="auto">
              {file ? file.name : "No file chosen"}
            </span>
          </div>
          <input
            ref={picker}
            type="file"
            accept={VIDEO_ACCEPT}
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
        <TextField
          label="Name"
          value={draft.name}
          onChange={(v) => edit("name", v)}
          error={problems.name}
          maxLength={LIMITS.name}
        />
        <TextField
          label="Credit"
          hint="Who made the video, as clips should name them."
          value={draft.credit}
          onChange={(v) => edit("credit", v)}
          error={problems.credit}
          maxLength={LIMITS.credit}
        />
        <TextField
          label="Licence"
          hint="Like CC BY 4.0, or your own footage."
          value={draft.license}
          onChange={(v) => edit("license", v)}
          error={problems.license}
          maxLength={LIMITS.license}
        />
        <TextField
          label="Licence link"
          hint="Optional. Where the licence or the video's page is."
          value={draft.license_url}
          onChange={(v) => edit("license_url", v)}
          error={problems.license_url}
          maxLength={LIMITS.license_url}
        />
        <TextField
          label="Notes"
          value={draft.notes}
          onChange={(v) => edit("notes", v)}
          error={problems.notes}
          maxLength={LIMITS.notes}
          multiline
        />
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
            <div
              className="h-full bg-accent transition-[width]"
              style={{ width: `${Math.round(progress * 100)}%` }}
            />
          </div>
        )}
        <DialogFooter>
          <Button
            disabled={busy}
            onClick={() => {
              reset();
              onClose();
            }}
          >
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? `Uploading ${Math.round((progress ?? 0) * 100)}%` : "Add"}
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  );
}

// Sound effects

function Effects() {
  return (
    <div className="flex max-w-xl flex-col gap-3">
      <p className="text-sm text-muted">
        ClipForge makes these itself. They play on a cold open, a big layout
        change, an emoji and the payoff when sound effects are on.
      </p>
      <SoundList />
    </div>
  );
}

// Fonts

const SAMPLE = "Nobody starts before the deadline";

// Each font file's face, added to the page once, by a family name of its own.
const faces = new Map<string, Promise<FontFace>>();

export function faceName(file: string): string {
  return `ClipForge ${stem(file)}`;
}

function loadFace(font: Font): Promise<FontFace> {
  let face = faces.get(font.file);
  if (!face) {
    const made = new FontFace(faceName(font.file), `url("${font.url}")`);
    document.fonts.add(made);
    face = made.load();
    faces.set(font.file, face);
  }
  return face;
}

function Fonts() {
  const fonts = useQuery({
    queryKey: key.fonts,
    queryFn: ({ signal }) => api<{ fonts: Font[] }>("/api/fonts", { signal }),
  });
  if (fonts.isPending)
    return <p className="text-sm text-muted">Loading the fonts.</p>;
  if (fonts.isError)
    return <p className="text-sm text-bad">{sentence(fonts.error)}</p>;
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted">
        The caption fonts that come with ClipForge, each free to use under its
        licence.
      </p>
      <ul className="flex flex-col gap-2">
        {fonts.data.fonts.map((font) => (
          <FontRow key={font.file} font={font} />
        ))}
      </ul>
    </div>
  );
}

function FontRow({ font }: { font: Font }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    loadFace(font).catch(() => setFailed(true));
  }, [font]);
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 rounded-card border border-line bg-panel px-4 py-3">
      <div className="flex min-w-0 flex-col gap-1">
        <span className="text-xs text-muted">
          {font.family} {font.style}
        </span>
        <span
          data-testid="font-sample"
          className="truncate text-2xl"
          style={{ fontFamily: `"${faceName(font.file)}", sans-serif` }}
        >
          {SAMPLE}
        </span>
        {failed && (
          <span className="text-xs text-bad">
            This font didn't load. Reload the window to try again.
          </span>
        )}
      </div>
      {font.licence && font.licence_url && (
        <a
          href={font.licence_url}
          target="_blank"
          rel="noreferrer"
          className="text-sm text-muted underline underline-offset-2"
        >
          {font.licence}
        </a>
      )}
    </li>
  );
}
