// The editor's Post copy tab: each app's caption and hashtags, counted as the app counts the whole post
// (caption, credit and hashtags together, in UTF-16 units). It isn't in the video, so each field saves as
// it's left instead of waiting for a render.

import { RotateCcw } from "lucide-react";
import { useState } from "react";

import { ApiError, api, sentence } from "../api.ts";
import { parseTags, POST_MAX, postText, same } from "../lib/editor.ts";
import type { ClipDetail } from "../types.ts";
import { Button } from "./Button.tsx";
import { TextField } from "./Field.tsx";
import { useToast } from "./Toasts.tsx";

type App = "tiktok" | "instagram";
type Part = "caption" | "hashtags";

const APPS: { app: App; name: string }[] = [
  { app: "tiktok", name: "TikTok" },
  { app: "instagram", name: "Instagram" },
];

type Props = { detail: ClipDetail; onSaved: (detail: ClipDetail) => void };

export function CopyTab({ detail, onSaved }: Props) {
  return (
    <div className="flex flex-col gap-6">
      {APPS.map(({ app, name }) => (
        <AppCopy key={app} app={app} name={name} detail={detail} onSaved={onSaved} />
      ))}
    </div>
  );
}

function AppCopy({ app, name, detail, onSaved }: Props & { app: App; name: string }) {
  const toast = useToast();
  const [drafts, setDrafts] = useState<Partial<Record<Part, string>>>({});
  const [errors, setErrors] = useState<Partial<Record<Part, string>>>({});
  const [saved, setSaved] = useState(false);
  const packaging = detail.packaging;
  const savedCaption = packaging?.[`${app}_caption` as const] ?? "";
  const savedTags = packaging?.[`${app}_hashtags` as const] ?? [];
  const caption = drafts.caption ?? savedCaption;
  const tagsText = drafts.hashtags ?? savedTags.join(" ");
  const tags = drafts.hashtags === undefined ? savedTags : parseTags(drafts.hashtags);
  const edited = [`${app}_caption`, `${app}_hashtags`].some((k) => detail.edits?.[k] != null);
  const n = postText(caption, tags, detail.project.credit).length; // a JS string's length is its UTF-16 units

  function edit(part: Part, text: string) {
    setDrafts((d) => ({ ...d, [part]: text }));
    setErrors((e) => ({ ...e, [part]: undefined }));
    setSaved(false);
  }

  async function send(changes: Record<string, unknown>, parts: Part[], sent: Partial<Record<Part, string>>) {
    try {
      const updated = await api<ClipDetail>(`/api/clips/${encodeURIComponent(detail.id)}`, {
        method: "PATCH",
        body: JSON.stringify({ edits: changes }),
      });
      onSaved(updated);
      // A draft typed on while it saved stays a draft.
      setDrafts((d) => {
        const next = { ...d };
        for (const part of parts) if (next[part] === sent[part]) delete next[part];
        return next;
      });
      setSaved(true);
    } catch (err) {
      const field = err instanceof ApiError ? (err.field ?? "") : "";
      const part: Part | null = field.startsWith(`edits.${app}_`) ? (field.endsWith("hashtags") ? "hashtags" : "caption") : null;
      if (part) setErrors((e) => ({ ...e, [part]: (err as ApiError).message }));
      else toast({ text: sentence(err), kind: "bad" });
    }
  }

  function leave(part: Part) {
    const draft = drafts[part];
    if (draft === undefined) return;
    const value = part === "caption" ? draft : parseTags(draft);
    const was = part === "caption" ? savedCaption : savedTags;
    if (same(value, was)) {
      setDrafts((d) => ({ ...d, [part]: undefined }));
      return;
    }
    void send({ [`${app}_${part}`]: value }, [part], { [part]: draft });
  }

  function reset() {
    void send({ [`${app}_caption`]: null, [`${app}_hashtags`]: null }, ["caption", "hashtags"], drafts);
  }

  return (
    <fieldset className="flex min-w-0 flex-col gap-3">
      <legend className="mb-1 text-sm font-semibold">{name}</legend>
      <TextField
        label={`${name} caption`}
        value={caption}
        onChange={(text) => edit("caption", text)}
        onBlur={() => leave("caption")}
        multiline
        count={{ n, max: POST_MAX, id: app }}
        hint="Counted with the credit and the hashtags, as the app counts the post."
        error={errors.caption}
      />
      <TextField
        label={`${name} hashtags`}
        value={tagsText}
        onChange={(text) => edit("hashtags", text)}
        onBlur={() => leave("hashtags")}
        error={errors.hashtags}
      />
      {detail.project.credit && (
        <p className="text-xs text-faint" dir="auto">
          The credit goes under the caption: {detail.project.credit}
        </p>
      )}
      <div className="flex items-center gap-3">
        <Button
          variant="ghost"
          disabled={!edited && drafts.caption === undefined && drafts.hashtags === undefined}
          onClick={reset}
        >
          <RotateCcw aria-hidden size={14} />
          {`Reset ${name} to the AI's`}
        </Button>
        {saved && (
          <span data-testid={`${app}-saved`} role="status" className="text-xs text-ok">
            Saved
          </span>
        )}
      </div>
    </fieldset>
  );
}
