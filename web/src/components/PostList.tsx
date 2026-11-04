// Today's Post block (SPEC 14.10 item 4): each exported post with its account, its hook and caption, and
// Open in TikTok (or Instagram), which copies the caption, opens the platform's upload page and shows the
// clip in Finder; then Mark as posted, with its link. A post TikTok Studio holds says when it goes up.
// Hooks and captions are only ever text.

import { useId, useState } from "react";

import { api, sentence } from "../api.ts";
import { PLATFORMS } from "../lib/accounts.ts";
import { putUpLine, scheduledLine } from "../lib/today.ts";
import type { PostToPutUp } from "../types.ts";
import { Button } from "./Button.tsx";
import { MarkPosted } from "./PostRow.tsx";
import { useToast } from "./Toasts.tsx";

export function PostList({ posts }: { posts: PostToPutUp[] }) {
  const id = useId();
  return (
    <section id="post" aria-labelledby={id} className="flex scroll-mt-4 flex-col gap-3">
      <h2 id={id} className="text-base font-semibold">
        Post
      </h2>
      {posts.map((post) => (
        <Row key={post.post_id} post={post} />
      ))}
    </section>
  );
}

function Row({ post }: { post: PostToPutUp }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [marking, setMarking] = useState(false);
  const site = PLATFORMS[post.platform];
  const who = [post.handle, site].filter(Boolean).join(" on ");

  async function putUp() {
    setBusy(true);
    let copied = true;
    try {
      await navigator.clipboard.writeText(post.full_caption);
    } catch {
      copied = false;
    }
    try {
      await api(`/api/posts/${encodeURIComponent(post.post_id)}/open-upload`, { method: "POST" });
      toast({ text: putUpLine(post.has_file, copied), kind: "ok" });
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <article
      aria-label={post.hook || "A post"}
      className="flex flex-col gap-3 rounded-card border border-line bg-panel p-4"
    >
      <div className="flex min-w-0 flex-col gap-0.5">
        <h3 className="truncate text-sm font-semibold" dir="auto">
          {post.hook || "A post"}
        </h3>
        <p className="text-xs text-muted" dir="auto">
          {who}
        </p>
      </div>
      <p dir="auto" className="whitespace-pre-line text-sm">
        {post.full_caption}
      </p>
      {post.status === "scheduled_on_tiktok" ? (
        <p className="text-sm text-muted">{scheduledLine(post.scheduled_at)}</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" onClick={() => void putUp()} disabled={busy}>
            {`Open in ${site}`}
          </Button>
          <Button variant="secondary" onClick={() => setMarking(true)}>
            Mark as posted
          </Button>
        </div>
      )}
      {marking && <MarkPosted post={{ id: post.post_id }} onClose={() => setMarking(false)} />}
    </article>
  );
}
