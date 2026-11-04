// What the whole window shares: the cache of what the server sent, the live progress of running jobs, and
// the queries more than one part of the window reads.

import { QueryClient, queryOptions } from "@tanstack/react-query";

import { ApiError, api, key } from "./api.ts";
import { triangleCount } from "./lib/campaigns.ts";
import { liveJobs } from "./lib/live.ts";
import type {
  Account,
  ActiveJob,
  AdviceBody,
  BriefSourcesBody,
  BriefsBody,
  CampaignBody,
  CampaignsBody,
  Check,
  ClipChannel,
  DraftsBody,
  MoneyBody,
  PostsBody,
  SettingsBody,
  TodayBody,
} from "./types.ts";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000, // the event stream says when something changed; this is only a safety net
      // A refusal (a 4xx) comes back the same however often it's asked; a dropped connection may not.
      retry: (count, err) =>
        count < 2 &&
        !(err instanceof ApiError && err.status >= 400 && err.status < 500),
    },
  },
});

export const live = liveJobs();

export const settingsQuery = queryOptions({
  queryKey: key.settings,
  queryFn: ({ signal }) => api<SettingsBody>("/api/settings", { signal }),
});

export const activeJobsQuery = queryOptions({
  queryKey: key.activeJobs,
  queryFn: ({ signal }) =>
    api<{ jobs: ActiveJob[] }>("/api/jobs?active=1", { signal }),
});

// Accounts, read by their page and by the Post dialog that picks where clips go.
export const accountsQuery = queryOptions({
  queryKey: key.accounts,
  queryFn: ({ signal }) =>
    api<{ accounts: Account[] }>("/api/accounts", { signal }),
});

// The Doctor runs ffmpeg, yt-dlp and claude to answer, so its answer is kept a while: Create's notice and
// Settings share it, and Run again or a yt-dlp update asks afresh.
export const doctorQuery = queryOptions({
  queryKey: key.doctor,
  queryFn: ({ signal }) => api<{ checks: Check[] }>("/api/doctor", { signal }),
  staleTime: 5 * 60_000,
});

// Every post, for the Queue; a post's event refreshes it.
export const postsQuery = queryOptions({
  queryKey: key.posts,
  queryFn: ({ signal }) => api<PostsBody>("/api/posts", { signal }),
});

// How many posts need you, for the rail on every page; under key.posts, so the same events refresh it.
export const needsYouQuery = queryOptions({
  queryKey: key.needsYou,
  queryFn: ({ signal }) =>
    api<PostsBody>("/api/posts?status=needs_you", { signal }),
  select: (body) => body.needs_you,
});

// The drafts waiting for review, grouped for Today, and when the next check runs.
export const draftsQuery = queryOptions({
  queryKey: key.drafts,
  queryFn: ({ signal }) => api<DraftsBody>("/api/drafts", { signal }),
});

// How many drafts wait, for Today's badge in the rail; the same query as Today's, so one answer serves both.
export const draftCountQuery = queryOptions({
  ...draftsQuery,
  select: (body: DraftsBody) => body.count,
});

// The clipping channels, for Today and their page.
export const channelsQuery = queryOptions({
  queryKey: key.channels,
  queryFn: ({ signal }) =>
    api<{ channels: ClipChannel[] }>("/api/clip-channels", { signal }),
});

// The Campaigns sheet, and one campaign with its submissions.
export const campaignsQuery = queryOptions({
  queryKey: key.campaigns,
  queryFn: ({ signal }) => api<CampaignsBody>("/api/campaigns", { signal }),
});

// The Money page's campaign posts and the accounts to filter them by.
export const moneyQuery = queryOptions({
  queryKey: key.money,
  queryFn: ({ signal }) => api<MoneyBody>("/api/money", { signal }),
});

// The briefs the marketplaces list, the richest first, and the sources they come from.
export const briefsQuery = queryOptions({
  queryKey: key.briefs,
  queryFn: ({ signal }) => api<BriefsBody>("/api/briefs", { signal }),
});

export const briefSourcesQuery = queryOptions({
  queryKey: key.briefSources,
  queryFn: ({ signal }) =>
    api<BriefSourcesBody>("/api/brief-sources", { signal }),
});

// Accounts worth adding, and the accounts that could take a brief today.
export const adviceQuery = queryOptions({
  queryKey: key.advice,
  queryFn: ({ signal }) => api<AdviceBody>("/api/advice", { signal }),
});

// How many campaigns have a triangle, for the rail.
export const campaignCountQuery = queryOptions({
  ...campaignsQuery,
  select: (body: CampaignsBody) => triangleCount(body.campaigns),
});

export function campaignQuery(id: string) {
  return queryOptions({
    queryKey: key.campaign(id),
    queryFn: ({ signal }) =>
      api<CampaignBody>(`/api/campaigns/${encodeURIComponent(id)}`, { signal }),
  });
}

// Today's list with its minutes, and the board around it.
export const todayQuery = queryOptions({
  queryKey: key.today,
  queryFn: ({ signal }) => api<TodayBody>("/api/today", { signal }),
});
