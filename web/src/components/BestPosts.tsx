// Yesterday's best (SPEC 14.10 item 8): the three posts that gained the most views in a day, each with its
// hook, its account, what it gained and its total, linking to the live post. Hooks are only ever text.

import { useId } from "react";

import { PLATFORMS } from "../lib/accounts.ts";
import { count } from "../lib/campaigns.ts";
import { gainLine } from "../lib/today.ts";
import type { BestPost } from "../types.ts";

export function BestPosts({ posts }: { posts: BestPost[] }) {
  const id = useId();
  return (
    <section id="best" aria-labelledby={id} className="flex scroll-mt-4 flex-col gap-3">
      <h2 id={id} className="text-base font-semibold">
        Yesterday's best
      </h2>
      <div className="grid gap-3 sm:grid-cols-3">
        {posts.map((post) => (
          <article
            key={post.post_id}
            aria-label={post.hook || "A post"}
            className="flex flex-col gap-1 rounded-card border border-line bg-panel p-4"
          >
            <h3 dir="auto" className="text-sm font-semibold">
              {post.hook || "A post"}
            </h3>
            <p dir="auto" className="text-xs text-muted">
              {[post.handle, PLATFORMS[post.platform]].filter(Boolean).join(" on ")}
            </p>
            <p className="text-base font-semibold tabular-nums">{gainLine(post.gained)}</p>
            <p className="text-xs text-muted tabular-nums">{`${count(post.views)} ${post.views === 1 ? "view" : "views"} in all`}</p>
            {post.url && (
              <a href={post.url} target="_blank" rel="noreferrer" className="text-sm text-fg underline underline-offset-2">
                Open the post
              </a>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
