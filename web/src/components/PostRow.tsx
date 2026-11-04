// One post in the Queue (SPEC 14.6): its status, clip, account, time and sentence, and the buttons it has
// from where it is (one TikTok Studio holds says what cancelling it means). Cancel, Export, Publish anyway
// and Post without the sound ask first; Reschedule, Edit caption and Mark as posted open a small form; Log opens the post's steps and
// screenshots.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { type ReactNode, useState } from "react";
import { Link } from "react-router";

import { ApiError, api, key, sentence } from "../api.ts";
import { PLATFORMS } from "../lib/accounts.ts";
import {
  CAPTION_LIMIT,
  captionUnits,
  dayThenTime,
  hashtagText,
  localInput,
  parseHashtags,
  parseLocal,
  timeZone,
} from "../lib/plan.ts";
import { type Action, actionsFor, cancelText, heldLine, statusOf } from "../lib/queue.ts";
import type { PostsBody, QueuedPost } from "../types.ts";
import { Badge } from "./Badge.tsx";
import { Button } from "./Button.tsx";
import { type Ask, useConfirm } from "./Confirm.tsx";
import { Details } from "./Details.tsx";
import { Dialog, DialogFooter } from "./Dialog.tsx";
import { DateTimeField, TextField } from "./Field.tsx";
import { PostLog } from "./PostLog.tsx";
import { useToast } from "./Toasts.tsx";

type Form = "reschedule" | "edit" | "mark_posted" | "log";

const LABELS: Record<Action, string> = {
  front: "Bring the window to the front",
  login: "Log in",
  publish_anyway: "Publish anyway",
  retry: "Retry",
  mark_posted: "Mark as posted",
  edit: "Edit caption",
  without_sound: "Post without the sound",
  reschedule: "Reschedule",
  export: "Export",
  cancel: "Cancel post",
  log: "Log",
};

const PATHS: Partial<Record<Action, string>> = { retry: "retry", export: "export", publish_anyway: "publish-anyway" };

const CANCEL = { title: "Cancel this post?", action: "Cancel the post", keep: "Keep it", danger: true }; // with cancelText

const ASKS: Partial<Record<Action, Ask>> = {
  export: {
    title: "Export this post?",
    description: "ClipForge won't post it. The video and its caption go in a folder for you to post yourself.",
    action: "Export",
  },
  publish_anyway: {
    title: "Publish anyway?",
    description:
      "The check found material that may belong to someone else. Posting it can get the post taken down " +
      "or the account a strike.",
    action: "Publish anyway",
    danger: true,
  },
};

// What Post without the sound asks first: the post goes with the clip's own audio only.
function withoutSound(post: QueuedPost): Ask {
  const title = post.options.sound?.title ?? "the sound";
  const when =
    post.status === "scheduled" ? "It goes up at its time" : "It's scheduled again at the next free slot";
  return {
    title: "Post without the sound?",
    description: `${when} with the clip's own audio only, and Instagram doesn't add ${title}.`,
    action: "Post without it",
    keep: "Keep the sound",
  };
}

export function accountText(post: Pick<QueuedPost, "account_name">): string {
  return post.account_name ?? "a removed account";
}

export function PostRow({ post, now }: { post: QueuedPost; now: number }) {
  const client = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const [form, setForm] = useState<Form | null>(null);
  const status = statusOf(post);
  const name = `${post.clip_title} to ${accountText(post)}`;
  const sentenceText = post.error || post.waiting || post.note;
  const held = heldLine(post);

  const act = useMutation({
    mutationFn: async (action: Action) => {
      if (action === "front") return api<undefined>("/api/posting/front", { method: "POST" });
      if (action === "login") return api<undefined>(`/api/accounts/${encodeURIComponent(post.account_id)}/login`, { method: "POST" });
      if (action === "cancel") return change(post.id, { status: "cancelled" });
      if (action === "without_sound" && post.status === "scheduled") return change(post.id, { drop_sound: true });
      if (action === "without_sound") {
        const body = JSON.stringify({ drop_sound: true });
        return api<{ post: QueuedPost }>(`/api/posts/${post.id}/retry`, { method: "POST", body });
      }
      return api<{ post: QueuedPost; folder?: string }>(`/api/posts/${post.id}/${PATHS[action]}`, { method: "POST" });
    },
    onSuccess: (body, action) => {
      if (action === "front") return void toast({ text: "Bringing the window to the front.", kind: "info" });
      if (action === "login") {
        void client.invalidateQueries({ queryKey: key.activeJobs });
        return void toast({ text: `Log in to ${PLATFORMS[post.platform]} in the Chrome window that opens.`, kind: "info" });
      }
      if (!body) return;
      kept(client, body.post);
      const when = dayThenTime(body.post.scheduled_at, Date.now() / 1000);
      if (action === "retry") toast({ text: `Scheduled again for ${when}`, kind: "ok" });
      if (action === "without_sound") {
        const text = post.status === "scheduled" ? "The sound is off this post" : `Scheduled again for ${when}, without the sound`;
        toast({ text, kind: "ok" });
      }
      if (action === "export" && "folder" in body) toast({ text: `Exported to ${body.folder}.`, kind: "ok" });
      if (action === "publish_anyway") toast({ text: "Posting it anyway.", kind: "ok" });
    },
    onError: (err) => {
      toast({ text: sentence(err), kind: "bad" });
      void client.invalidateQueries({ queryKey: key.posts });
    },
  });

  async function press(action: Action) {
    if (action === "edit" || action === "reschedule" || action === "mark_posted" || action === "log") {
      setForm(action);
      return;
    }
    const question =
      action === "cancel"
        ? { ...CANCEL, description: cancelText(post) }
        : action === "without_sound"
          ? withoutSound(post)
          : ASKS[action];
    if (question && !(await ask(question))) return;
    act.mutate(action);
  }

  const buttons = (
    <div className="flex flex-wrap items-center gap-2">
      {actionsFor(post).map((action) => (
        <Button
          key={action}
          variant={action === "log" ? "ghost" : action === "login" ? "primary" : "secondary"}
          disabled={act.isPending}
          onClick={() => void press(action)}
        >
          {LABELS[action]}
        </Button>
      ))}
      {post.url && (
        <a
          href={post.url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 rounded-sm text-sm text-fg underline underline-offset-2 hover:decoration-2"
        >
          <ExternalLink aria-hidden size={14} />
          Open the post
        </a>
      )}
    </div>
  );

  return (
    <article aria-label={name} className="flex flex-col gap-2 rounded-card border border-line bg-panel p-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Badge tone={status.tone}>{status.text}</Badge>
        <Link to={`/clips/${post.clip_id}`} dir="auto" className="min-w-0 truncate font-medium hover:underline">
          {post.clip_title}
        </Link>
        <span className="text-sm text-muted">
          to <span dir="auto">{accountText(post)}</span> on {PLATFORMS[post.platform]}
        </span>
        <span className="ml-auto text-sm text-muted tabular-nums">
          {dayThenTime(post.posted_at ?? post.scheduled_at, now)}
        </span>
      </div>
      {post.caption && (
        <p dir="auto" className="line-clamp-2 text-sm text-muted">
          {post.caption}
        </p>
      )}
      {sentenceText && (
        <p dir="auto" className={`text-sm ${post.status === "failed" ? "text-bad" : "text-fg"}`}>
          {sentenceText}
        </p>
      )}
      {held && <p className="text-sm text-muted">{held}</p>}
      {post.details ? <Details text={post.details}>{buttons}</Details> : buttons}
      {form === "reschedule" && <Reschedule post={post} onClose={() => setForm(null)} />}
      {form === "edit" && <EditCaption post={post} onClose={() => setForm(null)} />}
      {form === "mark_posted" && <MarkPosted post={post} onClose={() => setForm(null)} />}
      {form === "log" && <PostLog post={post} title={name} onClose={() => setForm(null)} />}
    </article>
  );
}

function change(id: string, body: Record<string, unknown>) {
  return api<{ post: QueuedPost }>(`/api/posts/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

// The post as the engine now has it, in the Queue at once; the refresh after it brings the counts along.
function kept(client: ReturnType<typeof useQueryClient>, post: QueuedPost) {
  client.setQueryData<PostsBody>(key.posts, (old) =>
    old ? { ...old, posts: old.posts.map((p) => (p.id === post.id ? post : p)) } : old,
  );
  void client.invalidateQueries({ queryKey: key.posts });
}

type FormProps = { post: QueuedPost; onClose: () => void };

// A small form in a dialog that saves one change; the engine's refusal stays in it, at its field.
function useChange(post: Pick<QueuedPost, "id">, onClose: () => void, done?: (post: QueuedPost) => void) {
  const client = useQueryClient();
  const [problem, setProblem] = useState<{ field: string | null; text: string } | null>(null);
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) => change(post.id, body),
    onSuccess: ({ post: saved }) => {
      kept(client, saved);
      void client.invalidateQueries({ queryKey: key.today });
      done?.(saved);
      onClose();
    },
    onError: (err) => setProblem({ field: err instanceof ApiError ? err.field : null, text: sentence(err) }),
  });
  return { save, problem, setProblem };
}

type FormDialogProps = { title: string; description: string; onClose: () => void; children: ReactNode };

function FormDialog({ title, description, onClose, children }: FormDialogProps) {
  return (
    <Dialog
      title={title}
      description={description}
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      {children}
    </Dialog>
  );
}

function Reschedule({ post, onClose }: FormProps) {
  const [text, setText] = useState(localInput(post.scheduled_at));
  const { save, problem, setProblem } = useChange(post, onClose);
  function submit() {
    const at = parseLocal(text);
    if (at === null) return setProblem({ field: "scheduled_at", text: "Pick a date and time." });
    save.mutate({ scheduled_at: at });
  }
  return (
    <FormDialog title="Reschedule the post" description={`The time is in ${timeZone()}.`} onClose={onClose}>
      <DateTimeField label="New time" value={text} onChange={setText} error={problem?.text} />
      <DialogFooter>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" disabled={save.isPending} onClick={submit}>
          Save
        </Button>
      </DialogFooter>
    </FormDialog>
  );
}

function EditCaption({ post, onClose }: FormProps) {
  const [caption, setCaption] = useState(post.caption);
  const [tags, setTags] = useState(hashtagText(post.hashtags));
  const { save, problem } = useChange(post, onClose);
  const hashtags = parseHashtags(tags);
  return (
    <FormDialog
      title="Edit the caption"
      description={`For ${accountText(post)} on ${PLATFORMS[post.platform]}.`}
      onClose={onClose}
    >
      <TextField
        label="Caption"
        value={caption}
        onChange={setCaption}
        count={{ n: captionUnits(caption, hashtags), max: CAPTION_LIMIT[post.platform], id: `caption-${post.id}` }}
        hint={`Counted with the hashtags, as ${PLATFORMS[post.platform]} counts it.`}
        error={problem && problem.field !== "hashtags" ? problem.text : undefined}
        multiline
        maxLength={10_000}
      />
      <TextField
        label="Hashtags"
        value={tags}
        onChange={setTags}
        hint="Separate them with spaces."
        error={problem?.field === "hashtags" ? problem.text : undefined}
        maxLength={2000}
      />
      <DialogFooter>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" disabled={save.isPending} onClick={() => save.mutate({ caption, hashtags })}>
          Save
        </Button>
      </DialogFooter>
    </FormDialog>
  );
}

// Also Today's, for a post put up by hand from its export.
export function MarkPosted({ post, onClose }: { post: Pick<QueuedPost, "id">; onClose: () => void }) {
  const [link, setLink] = useState("");
  const toast = useToast();
  const { save, problem } = useChange(post, onClose, () => toast({ text: "Marked as posted", kind: "ok" }));
  return (
    <FormDialog
      title="Mark as posted"
      description="For a post you finished on the site yourself, or put up from its export. Paste its address so the Queue links to it."
      onClose={onClose}
    >
      <TextField label="The post's link" value={link} onChange={setLink} error={problem?.text} maxLength={2048} />
      <DialogFooter>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="primary"
          disabled={save.isPending}
          onClick={() => save.mutate(link.trim() ? { status: "posted", url: link.trim() } : { status: "posted" })}
        >
          Mark as posted
        </Button>
      </DialogFooter>
    </FormDialog>
  );
}
