// The shapes the server sends (clipforge/server/views.py, routes and jobs/runner.py's job_event). Its times
// are seconds since 1970, as the engine keeps them.

export type Quality = "best" | "balanced" | "fast";

export type CaptionPreset =
  "bold_pop" | "hormozi" | "beast" | "pill" | "clean" | "one_word" | "neon";

export type ProjectOptions = {
  length: "auto" | "short" | "standard" | "long";
  clip_count: number | null;
  content_type: "auto" | "podcast" | "solo" | "stream" | "other";
  prompt: string | null;
  quality: Quality | null;
  credit: boolean | null;
  render: boolean;
  provider: string | null;
  layout:
    "auto" | "face" | "stacked" | "gameplay" | "stream" | "fit" | "motion";
  caption_preset: CaptionPreset | null;
  as_is: boolean; // Post as it is: each video is one clip, uncut (SPEC 14.2)
};

export type StepStatus = "done" | "failed" | "running" | "waiting" | "todo";

export type Step = {
  key: string;
  label: string;
  status: StepStatus;
  progress: number;
};

export type Project = {
  id: string;
  title: string | null;
  channel: string | null;
  channel_url: string | null;
  source_url: string | null;
  source_kind: string;
  duration_s: number | null;
  language: string | null;
  rights_basis: string;
  status: string;
  stage: string | null;
  progress: number;
  message: string | null;
  error: string | null;
  error_code: string | null;
  error_details: string | null;
  options: ProjectOptions;
  created_at: number;
  updated_at: number;
  thumb: string | null;
  clip_count: number;
  rendered_count: number;
  rerun_drops: number; // the rendered clips a rerun deletes: those without edits or posts, set-aside ones too
  source_on_disk: boolean;
  steps: Step[];
  campaign_id: string | null; // the paid campaign its clips are made for
};

export type Tier = "post" | "test" | "weak" | "no";

export type ClipStatus =
  | "idea"
  | "packaged"
  | "queued"
  | "analyzing"
  | "rendering"
  | "rendered"
  | "failed";

export type Flag = { code: string; text: string; level: "red" | "soft" };

// A rendered version as the window plays it.
export type Render = {
  id: number;
  version: number;
  poster: string | null;
  thumb: string | null;
  video: string | null;
  duration_s: number | null;
  size_bytes: number | null;
  qa_ok: boolean;
};

// A clip as its card shows it. ``progress`` is its running job's, ``edited`` whether it has edits of the
// user's own, and ``stale`` whether its render was made from other edits than it has now.
export type Clip = {
  id: string;
  project_id: string;
  rank: number | null;
  start_s: number;
  end_s: number;
  duration_s: number;
  final_score: number | null;
  tier: Tier | null;
  passes_gates: boolean;
  archetype: string | null;
  reason: string | null;
  hook_title: string | null;
  layout: string | null;
  layout_reason: string | null;
  flags: Flag[];
  notes: string[];
  status: ClipStatus;
  error: string | null;
  render: Render | null;
  progress: number | null;
  edited: boolean;
  stale: boolean;
  discarded: boolean;
  posted_to: PostedTo[];
  as_is: boolean; // the whole video as it is: nothing edits or renders it
};

// Where a clip went up (clipforge/server/views.py posted_to), oldest first.
export type PostedTo = {
  post_id: string;
  account_id: string;
  platform: Platform;
  handle: string;
  url: string | null;
};

export type FactCard = { sid: string; text: string };

// A clip's packaging (clipforge/models.py Packaging): the AI's, or the AI's with Zyad's edits over it.
export type Packaging = {
  hook_title: string;
  hook_alts: string[];
  accent_word: string | null;
  fact_cards: FactCard[];
  tiktok_caption: string;
  tiktok_hashtags: string[];
  instagram_caption: string;
  instagram_hashtags: string[];
  credit: string | null;
};

export type RenderVersion = Render & { created_at: number };

// GET /api/clips/{id} (clipforge/server/views.py clip_detail).
export type ClipDetail = Clip & {
  scores: Record<string, number>;
  evidence: { criterion: string; quote: string }[];
  packaging: Packaging | null;
  packaging_ai: Packaging | null;
  edits: Record<string, unknown> | null;
  renders: RenderVersion[];
  sentence_ids: string[];
  cold_open_line: string | null; // the line a cold open would play, cleaned
  project: {
    id: string;
    title: string | null;
    duration_s: number | null;
    rights_basis: string;
    credit: string | null;
  };
};

export type TranscriptWord = {
  i: number;
  text: string;
  start: number;
  end: number;
  sid: string;
  filler: boolean;
  struck: boolean;
  fix: string | null;
  out: number | null; // where the current render says it, or null when it isn't in the render
};

// GET /api/clips/{id}/transcript: the clip's words and 30 s either side.
export type TranscriptWindow = {
  range: [number, number];
  orig_range: [number, number];
  window: [number, number];
  fps: number | null;
  words: TranscriptWord[];
  sentences: { id: string; start: number; end: number; in_clip: boolean }[];
};

// A gameplay video or a music track in the library, with how many clips use it
// (clipforge/server/routes/library.py). A track's artist is its credit and its title its name.
export type LibraryItem = {
  id: string;
  kind: string;
  name: string;
  credit: string | null;
  source_url: string | null;
  license: string | null;
  license_url: string | null;
  credit_required: boolean;
  moods: string[];
  energy: number | null;
  notes: string | null;
  duration_s: number | null;
  width: number | null;
  height: number | null;
  size_bytes: number | null;
  uses: number;
  url: string;
  created_at: number;
};

// A video on the gameplay starter list (clipforge/library/starter.py) and where its download stands.
export type StarterEntry = {
  id: string;
  game: string;
  title: string;
  uploader: string;
  video_id: string;
  duration_s: number;
  size: string;
  licence: string;
  credit: string;
  terms: string;
  personal_only: boolean;
  informal: boolean;
  chips: string[];
  first_part: boolean;
  state: "offered" | "downloading" | "added" | "failed";
  item_id: string | null;
  error: string | null;
  job_id?: number;
};

export type StarterList = { note: string; entries: StarterEntry[] };

export type Font = {
  file: string;
  family: string;
  style: string;
  licence: string | null;
  url: string;
  licence_url: string | null;
};

export type Sound = { kind: string; length_s: number; url: string };

export type Rejected = { input: string; message: string };

export type ProjectsAdded = { projects: Project[]; rejected: Rejected[] };

export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";

// A job's change as the event stream sends it.
export type JobEvent = {
  id: number;
  kind: string;
  ref_id: string;
  project: string | null;
  status: string;
  progress: number;
  message: string | null;
};

// A job as the jobs list shows it: never its kind, which is for code, only its label.
export type ActiveJob = JobEvent & {
  label: string;
  project_title: string | null;
  clip_rank: number | null;
  started_at: number | null;
};

export type Theme = "dark" | "light" | "system";

// The values the window reads by name; the Settings page reads the rest by field (clipforge/config.py).
export type SettingsValues = {
  theme: Theme;
  allow_other_sites: boolean;
  min_free_gb: number;
  create_options: ProjectOptions;
  download_quality: Quality;
  caption_preset: CaptionPreset;
  llm_provider: string;
  llm_models: Record<string, Record<string, string>>;
  llm_economy: boolean;
  accent_color: string | null;
  own_channels: string[];
  posting: PostingValues;
  watch: WatchValues;
  marketplaces: Record<string, Marketplace>;
  today: TodayValues;
  briefs: BriefValues;
  trends: TrendValues;
  [name: string]: unknown;
};

// Settings.trends (clipforge/config.py TrendSettings): whether SocialPilot's feed is read once a week for the
// New list out badge.
export type TrendValues = { list_ping: boolean };

// Settings.briefs (clipforge/config.py BriefSettings): when briefs are read each morning, how many Claude
// fills in, and the niches briefs and the advice are sorted into, each with its language and tags.
export type NicheValues = { name: string; language: string; tags: string[] };
export type BriefValues = {
  read_at: string;
  read_per_morning: number;
  niches: NicheValues[];
};

// Settings.today (clipforge/config.py TodaySettings): when the daily notification goes and whether it does,
// the switch for the others, the 7-day views guessed for a new account, and what one of each currency is
// worth in US$, and whether the path to your first payout shows.
export type TodayValues = {
  guide: boolean;
  notify_at: string;
  daily_notification: boolean;
  event_notifications: boolean;
  starting_views: number;
  rates: Record<string, number>;
};

// Settings.marketplaces (clipforge/config.py Marketplace): by name, where you withdraw and the least it pays out.
export type Marketplace = {
  withdraw_url: string | null;
  min_payout: number | null;
};

// Settings.watch (clipforge/config.py WatchSettings): the lengths a clipping channel skips, for every one.
export type WatchValues = { min_minutes: number; max_hours: number };

// Settings.posting (clipforge/config.py PostingSettings): when and how often each platform gets a post.
// ``paused`` is the kill switch, which only Pause posting and Resume change.
export type PostingValues = {
  paused: boolean;
  timezone: string;
  windows: PostingWindow[];
  tiktok_daily_cap: number;
  instagram_daily_cap: number;
  tiktok_spacing_h: number;
  instagram_spacing_h: number;
  platform_gap_min: number;
  jitter_min: number;
  graph_version: string;
  public_base_url: string | null;
  away_min: number;
  tiktok_schedule: boolean;
  telegram_alerts: boolean;
  telegram_target: string | null;
};

// The settings with each one's choices, the AI passes, and the models each provider uses when none is set.
export type SettingsBody = {
  values: SettingsValues;
  choices: Record<string, string[]>;
  passes: string[];
  models: Record<string, Record<string, string>>;
  economy_models: Record<string, string>;
};

// One of the Doctor's checks (clipforge/doctor.py): a required one failing means ClipForge can't work.
export type Check = {
  name: string;
  ok: boolean;
  detail: string;
  fix: string;
  required: boolean;
};

// The background service (SPEC 10.7): launchd's answer, and the sentence for an Install or Remove.
export type ServiceStatus = {
  installed: boolean;
  running: boolean;
  pid: number | null;
  sentence: string;
  message?: string;
};

export type UpdateResult = {
  ok: boolean;
  before: string;
  after: string;
  message: string;
  details: string;
};

export type Usage = {
  free_bytes: number;
  total_bytes: number;
  sources: number;
  work: number;
  renders: number;
  library: number;
  assets: number;
  logs: number;
  uploads: number;
};

export type StorageInfo = {
  usage: Usage;
  cleanable: { projects: number; bytes: number };
  delete_sources_after_days: number;
};

export type Cleaned = { freed_bytes: number; projects: number };

export type ErrorBody = {
  error: string;
  message: string;
  field: string | null;
};

// Accounts (SPEC 11): never a token, a Keychain item, a Chrome profile or Meta's ids, only how each one is.

export type Platform = "tiktok" | "instagram";

export type Method = "browser" | "api";

export type AccountStatus =
  "connected" | "needs_login" | "token_expiring" | "error";

export type PostingWindow = { start: string; end: string };

// The fonts captions can use (clipforge/assets.py FONTS).
export type FontName =
  | "montserrat-black"
  | "montserrat-extrabold"
  | "montserrat-bold"
  | "poppins-extrabold"
  | "anton"
  | "bangers"
  | "inter-extrabold"
  | "bebas-neue"
  | "archivo-black"
  | "luckiest-guy";

export type GameplayMode = "auto" | "off" | "always";

// An account's look (clipforge/render/styles.py StyleKit): each field set wins over the clip's own for its
// posts, and a null one keeps the clip's.
export type StyleKit = {
  preset: CaptionPreset | null;
  font: FontName | null;
  accent: string | null; // "#RRGGBB"
  hook_fill: string | null;
  hook_text: string | null;
  credit_format: string | null; // holds {channel}
  gameplay_mode: GameplayMode | null;
};

export type AccountSettings = {
  niche_tags: string[];
  assisted: boolean;
  hashtags: string[];
  cta: string;
  daily_cap: number | null; // null: the platform's default in Settings
  windows: PostingWindow[] | null; // null: the default windows in Settings
  pair_id: string | null;
  creator_rewards: boolean;
  style_kit: StyleKit;
};

export type Account = {
  id: string;
  platform: Platform;
  method: Method;
  label: string;
  handle: string | null;
  display_name: string | null;
  avatar: string | null;
  status: AccountStatus;
  status_note: string | null;
  paused: boolean;
  ig_mode: "facebook" | "instagram" | null;
  experimental: boolean;
  token_expires_at: number | null;
  last_checked_at: number | null;
  created_at: number;
  settings: AccountSettings;
  today: { posts: number; cap: number; ceiling: number }; // ceiling: the most a day its route posts
  last_post: {
    id: string;
    clip_title: string | null;
    url: string | null;
    posted_at: number | null;
  } | null;
};

// An Instagram account found on the Facebook Pages a pasted key can see, with its own id here when it is
// already linked.
export type Candidate = {
  ig_user_id: string;
  page_id: string;
  page_name: string;
  username: string;
  account_id: string | null;
};

export type Health = {
  status: AccountStatus;
  sentence: string;
  details: string | null;
};

// A sound on your TikTok shortlist or on an Instagram account's trending list (SPEC 13, views.py
// sound_json): its title and id there, never the sound itself; ``link_id`` is what Open takes.
export type TrendSound = {
  id: string;
  platform: Platform;
  source: "shortlist" | "ig_trending";
  external_id: string;
  title: string;
  artist: string | null;
  url: string | null;
  region: "AU" | "US" | null;
  mood_tags: string[];
  energy: number | null;
  audio_type: "music" | "original_sound" | null;
  duration_s: number | null;
  rank: number | null; // its place in Instagram's list, 0 first
  account_id: string | null;
  fetched_at: number | null;
  created_at: number;
  stale: boolean;
  link_id: string | null;
};

// A track of yours or a sound suggested for a clip, best first (clipforge/trends/recommend.py, views.py
// suggestion_json). A track has no id on a platform, no link and nothing to open.
export type SoundSuggestion = {
  kind: "music" | "shortlist" | "ig_trending";
  id: string;
  title: string;
  artist: string | null;
  why: string;
  stale: boolean;
  score: number;
  external_id: string | null;
  url: string | null;
  link_id: string | null;
};

// The sound picked for one post (clipforge/trends/models.py PostSound): Instagram adds a trending one at
// ``volume`` (0 to 100), and you add a shortlisted one yourself in TikTok's window.
export type PostSound = {
  source: "shortlist" | "ig_trending";
  external_id: string;
  title: string;
  artist: string | null;
  url: string | null;
  volume: number;
};

// When a list was last read (clipforge/trends/models.py TrendRead).
export type TrendRead = {
  id: string;
  read_at: number | null;
  newest_at: number | null;
  seen_at: number | null;
  result: "ok" | "error" | "blocked" | null;
  error: string | null;
};

export type HashtagSet = {
  id: string;
  niche: string;
  name: string;
  tags: string[];
  created_at: number;
  updated_at: number;
};

// The Trends page's lists (clipforge/server/routes/trends.py): each Instagram account linked through
// Facebook with its trending sounds, your TikTok shortlist, the four weekly lists to open, whether one is
// newer than your last look, your hashtag sets and the accounts Instagram gives no trending sounds to.
export type Trends = {
  instagram: {
    account: { id: string; handle: string | null };
    read: TrendRead | null;
    sounds: TrendSound[];
    can_check: boolean;
  }[];
  shortlist: TrendSound[];
  lists: { name: string; label: string; link_id: string }[];
  badge: boolean;
  sets: HashtagSet[];
  mode_b: (string | null)[];
};

// A post's switches (clipforge/post/models.py PostOptions): TikTok's visibility, comments, duet, stitch and
// content disclosure, and Instagram's share to feed. A post's sound is its own, never a platform block's.
export type PostOptions = {
  visibility: "everyone" | "friends" | "only_me";
  comments: boolean;
  duet: boolean;
  stitch: boolean;
  disclosure: boolean;
  share_to_feed: boolean;
  trial_reel: boolean; // Instagram through Meta's API
  trial_graduation: "manual" | "auto";
  assisted: boolean;
  sound?: PostSound | null;
};

// Only the switches sent change; the engine fills the rest from the plan. A campaign post's hand rules
// ticked (10.10) go whole, by the keys its rules give them.
export type PostEdit = {
  caption?: string;
  hashtags?: string[];
  options?: Partial<PostOptions>;
  rule_ticks?: string[];
  off_campaign?: boolean;
};

// What the Post dialog asks the engine to plan, and to make into posts once Confirm and post is pressed.
export type PlanRequest = {
  clip_ids: string[];
  account_ids: string[];
  mode: "spread" | "every";
  timing: "now" | "smart" | "custom";
  custom_start: number | null;
  custom_interval_min: number | null;
  custom_times: Record<string, number>;
  edits: Record<string, PostEdit>;
  platform_options: Partial<Record<Platform, Partial<PostOptions>>>; // Details' "Every TikTok post"
  acknowledged_every: boolean;
  draft_ids?: string[]; // approving drafts: the posts these become, which must be these clips and accounts
};

export type PlannedPost = {
  key: string; // "<clip id>:<account id>"
  clip_id: string;
  account_id: string;
  platform: Platform;
  caption: string;
  hashtags: string[];
  options: PostOptions;
  scheduled_at: number;
  warnings: string[];
  rules: RuleCheck[]; // its campaign's rules, checked; none when it has none
  rule_ticks: string[];
  off_campaign: boolean;
  campaign_id: string | null;
  campaign: { id: string; name: string } | null;
};

// Something the plan says, about one post (its key) or all of them; a blocking one stops Confirm and post.
export type PlanWarning = {
  code: string;
  sentence: string;
  key: string | null;
  blocking: boolean;
  move_to: number | null;
};

export type Plan = {
  plan_id: string;
  posts: PlannedPost[];
  warnings: PlanWarning[];
  blocked: boolean;
  renders: string[];
};

export type PostStatus =
  | "scheduled"
  | "rendering"
  | "uploading"
  | "processing"
  | "scheduled_on_tiktok" // uploaded with TikTok Studio's own schedule set; TikTok puts it up at its time
  | "posted"
  | "needs_you"
  | "failed"
  | "cancelled"
  | "exported";

// A post as the Queue shows it (clipforge/server/views.py post_json). ``actions`` are the extra choices the
// engine offers from where it is, like "publish_anyway" after a copyright check.
export type QueuedPost = {
  id: string;
  batch_id: string;
  clip_id: string;
  clip_title: string;
  account_id: string;
  account_name: string | null;
  platform: Platform;
  method: Method;
  caption: string;
  hashtags: string[];
  options: PostOptions;
  scheduled_at: number;
  status: PostStatus;
  attempts: number;
  url: string | null;
  error: string | null;
  error_code: string | null;
  details: string | null;
  note: string | null;
  actions: string[];
  retry_helps: boolean; // false for a failed post that pressing Retry can't change
  on_tiktok: boolean; // TikTok Studio holds it, so it is never uploaded again: no Retry, Reschedule or Export
  waiting: string | null; // what its Chrome window waits for you to do, while it does
  screenshot: string | null;
  posted_at: number | null;
  created_at: number;
  updated_at: number;
};

export type PostsBody = { posts: QueuedPost[]; needs_you: number };

// Clipping channels (SPEC 10.9 and 14.9, views.py channel_json): what each watches and fills, its last check
// and when it checks next, and its drafts waiting for review.
export type RightsBasis = "not_cleared" | "permission" | "clipping_programme";

export type WatchedSourceInfo = {
  id: string;
  title: string;
  url: string;
  newest_title: string | null;
  newest_at: number | null;
  error: string | null;
  letter: string; // the avatar until there is one
};

export type CheckInfo = {
  id: string;
  manual: boolean;
  started_at: number;
  finished_at: number | null;
  found: number;
  skipped: number;
  clipped: number;
  drafts: number;
  error: string | null;
  line: string;
};

// A video a check saw (views.py item_json), and why it wasn't clipped if it wasn't.
export type WatchItemInfo = {
  id: string;
  video_id: string;
  title: string;
  url: string;
  published_at: number | null;
  status: "new" | "skipped" | "waiting" | "clipping" | "drafted" | "failed";
  code: string | null;
  reason: string | null;
  project_id: string | null;
};

export type HistoryCheck = CheckInfo & { items: WatchItemInfo[] };

export type ClipChannel = {
  id: string;
  name: string;
  paused: boolean;
  account_ids: string[];
  options: { project: Partial<ProjectOptions>; hashtags: string[] };
  rights_basis: RightsBasis;
  check_time: string; // "06:00", in the posting time zone
  daily_video_limit: number;
  clips_per_day: number | null; // null: what the accounts can post
  on_limit: "wait" | "economy" | "basic";
  sources: WatchedSourceInfo[];
  last_check: CheckInfo | null;
  last_line: string | null;
  next_check_at: number | null; // null while it doesn't watch: paused, or nothing to watch
  check_now_after: number | null;
  drafts: number;
  campaign: { id: string; name: string; ended: string | null } | null; // ended: why, once it has
};

// A draft as Today's Clips to review shows it (views.py drafts_json): the post it becomes, with its clip.
export type Draft = Omit<QueuedPost, "status"> & {
  status: "draft";
  score: number | null;
  reasons: string[];
  media: Render | null;
  hook_title: string | null;
  warnings: PlanWarning[];
  slot_moved: { at: number; sentence: string } | null; // its slot passed, so approving it moves it here
  campaign: { id: string; name: string } | null;
  rules: RuleCheck[]; // the campaign's rules checked on this post, in their order
};

// One of a campaign's rules checked on a post: auto ones by ClipForge, hand ones ticked by you.
export type RuleCheck = {
  key: string;
  label: string;
  kind: "auto" | "hand";
  ok: boolean;
  sentence: string;
};

export type DraftVideo = {
  project: {
    id: string;
    title: string | null;
    channel: string | null;
    thumb: string | null;
    published_at: number | null;
    rights_basis: string;
  };
  drafts: Draft[];
};

export type DraftGroup = {
  channel: { id: string; name: string } | null;
  videos: DraftVideo[];
}; // null: removed

export type NextCheck = { at: number; name: string };

export type DraftsBody = {
  groups: DraftGroup[];
  count: number;
  next_check: NextCheck | null;
};

// One step of a post's log: its name, when (seconds since 1970) and what it noted.
export type PostLogStep = { step: string; at: number; [name: string]: unknown };

export type PostLog = { steps: PostLogStep[]; screenshots: string[] };

// A paid clipping campaign (SPEC 10.10 and 14.11) and its submissions, as views.py campaign_json and
// submission_json give them.
export type SubmissionStatus =
  "to_submit" | "submitted" | "approved" | "rejected" | "paid";

export type Banned =
  | "gameplay"
  | "music"
  | "sfx"
  | "emoji"
  | "hook"
  | "cards"
  | "progress_bar"
  | "punch_ins"
  | "credit_line";

export type FootageChannel = {
  yt_channel_id: string;
  title: string | null;
  url: string;
};

export type CampaignCounts = {
  made: number;
  approved: number; // by you
  live: number;
  submitted: number;
  accepted: number; // by the marketplace
  paid: number;
};

// null for views before any count, and for estimated and pending without a rate
export type CampaignMoney = {
  counts: CampaignCounts;
  views: number | null;
  estimated: number | null;
  pending: number | null;
  paid: number;
};

export type Required = {
  hashtags: string[];
  mentions: string[];
  text: string[];
};

export type Campaign = {
  id: string;
  name: string;
  marketplace: string;
  campaign_url: string | null;
  submit_url: string | null;
  currency: string;
  usd_rate: number; // US$ for 1 of its currency, through Settings' rates, 1 without one
  rate_per_1000: number | null;
  min_views: number | null;
  max_payout: number | null;
  platforms: Platform[];
  footage: { channels: FootageChannel[]; videos: string[]; files: boolean };
  required: Required;
  banned: Banned[];
  hand_rules: string[];
  min_s: number | null;
  max_s: number | null;
  ends_on: string | null; // "2026-11-30", its last day
  budget_left: number | null;
  budget_entered_on: string | null;
  status: "active" | "ended";
  notes: string;
  rules_text: string;
  created_at: number;
  updated_at: number;
  ended: string | null; // why it's over, as a sentence
  state: "active" | "ending" | "ended"; // ending: its last day is under 3 days away
  channels: { id: string; name: string }[];
  links: { page: string | null; submit: string | null }; // the ids Open sends, never the links
  money: CampaignMoney;
  todo: CampaignTodo[]; // its share of today's list, in the list's order
};

export type CampaignTodo = { kind: TodoKind; title: string; count: number };

export type MoneyLine = {
  marketplace: string;
  currency: string;
  estimated: number | null;
  pending: number | null;
  paid: number;
  min_payout: number | null;
  withdraw: boolean;
  ready: boolean;
  withdraw_link: string | null;
};

export type CampaignsBody = { campaigns: Campaign[]; totals: MoneyLine[] };

// A campaign post as the Money page shows it (SPEC 14.12): every amount in its campaign's currency.
export type MoneyPost = {
  post_id: string;
  posted_on: string; // "2026-11-03", on the posting time zone's clock
  account_id: string;
  handle: string | null;
  platform: Platform;
  niche: string | null; // the account's first niche tag
  campaign_id: string;
  campaign: string;
  marketplace: string;
  hook: string;
  url: string | null;
  views_2d: number | null;
  views_7d: number | null;
  views: number | null; // the newest count
  status: SubmissionStatus;
  reason: string | null; // why it was turned down
  currency: string;
  estimated: number | null; // ClipForge's estimate while submitted or approved
  pending: number | null; // the estimate while approved
  paid: number | null; // what you entered once paid
  rate: number; // US$ for 1 of its currency, through Settings' rates
  as_if: boolean; // no rate for its currency, so counted as if US$
};

export type MoneyAccount = {
  id: string;
  platform: Platform;
  handle: string | null;
  niche: string | null;
};

export type MoneyBody = {
  today: string;
  posts: MoneyPost[]; // newest posted first
  accounts: MoneyAccount[];
  as_if: string[]; // the currencies counted as if US$
};

export type Submission = {
  id: string;
  post_id: string;
  campaign_id: string;
  status: SubmissionStatus;
  submitted_at: number | null;
  decided_at: number | null;
  reason: string | null;
  paid_amount: number | null;
  paid_on: string | null;
  created_at: number;
  updated_at: number;
  moves: SubmissionStatus[];
  post: {
    id: string;
    platform: Platform;
    method: Method;
    account_name: string | null;
    clip_title: string | null;
    posted_at: number | null;
    url: string | null;
  } | null;
  views: number | null;
  views_source: "insights" | "hand" | null;
  views_at: number | null;
  estimate: number | null;
  views_due: string | null; // "2 days", "7 days" or "end date": the count asked for by hand today
};

export type CampaignBody = { campaign: Campaign; submissions: Submission[] };

// The form's fields as the engine reads pasted rules into them (parse.blank), and as Save sends them.
// A number field holds the text typed when it isn't a number, so the engine's sentence names the field.
export type CampaignFields = {
  name: string;
  marketplace: string;
  campaign_url: string | null;
  submit_url: string | null;
  currency: string;
  rate_per_1000: number | string | null;
  min_views: number | string | null;
  max_payout: number | string | null;
  platforms: Platform[];
  footage: { videos: string[]; files: boolean };
  footage_links: string[];
  required: Required;
  banned: Banned[];
  hand_rules: string[];
  min_s: number | string | null;
  max_s: number | string | null;
  ends_on: string | null;
  budget_left: number | string | null;
  notes: string;
  rules_text: string;
};

export type ParseResult = {
  fields: CampaignFields;
  filled: string[];
  sentence: string | null;
};

// Briefs (SPEC 10.11): the campaigns the marketplaces list, the richest first. Money is in the brief's
// currency; a platform's own rate or cap is in rates and caps, else the brief's own.
export type BriefStatus = "new" | "seen" | "hidden" | "used" | "gone";

export type BriefWorth = {
  amount: number | null; // what it could earn a day, null without a rate
  currency: string;
  guess: boolean;
  account_id: string | null; // the account it is worth that through, null when none fits
};

export type BriefFit = {
  id: string;
  platform: Platform;
  handle: string | null;
  label: string;
};

export type Brief = {
  id: string;
  source_id: string;
  external_id: string;
  url: string | null;
  marketplace: string;
  brand: string | null;
  title: string;
  niche: string | null; // a niche's name in Settings
  language: string | null;
  currency: string;
  rate_per_1000: number | null;
  rates: Partial<Record<Platform, number>>;
  min_views: number | null;
  max_payout: number | null;
  caps: Partial<Record<Platform, number>>;
  budget_total: number | null;
  budget_spent: number | null;
  budget_left: number | null;
  budget_read_at: number | null; // when the budget left was read
  ends_on: string | null;
  platforms: Platform[];
  footage: { kind: string | null; links: string[] };
  text: string;
  fields: Record<string, "stated" | "claude" | "guess">;
  notes: Record<string, string>; // Campaign Scout's own notes
  status: BriefStatus;
  campaign_id: string | null;
  first_seen_at: number;
  last_seen_at: number;
  worth: BriefWorth;
  fits: BriefFit[];
  openable: boolean;
  link_id: string | null; // what Open opens, null when ClipForge may not open its page
  reposting: boolean; // it asks you to post its ready-made videos as they are, so it comes first
};

export type BriefSource = {
  id: string;
  name: string;
  marketplace: string;
  list_url: string | null;
  reader: "campaign_scout" | "general" | "paste";
  enabled: boolean;
  terms_checked_on: string | null;
  terms_note: string | null;
  last_run_at: number | null;
  last_result: "ok" | "blocked" | "sign_in" | "changed" | "error" | null;
  error: string | null;
  created_at: number;
  updated_at: number;
  check_now_after: number | null; // when Check now opens again, null when it is open now
  reading: boolean;
  paste: boolean; // its briefs are to be pasted today
};

export type BriefsBody = { briefs: Brief[] };
export type BriefSourcesBody = { sources: BriefSource[] };

// Accounts worth adding (SPEC 10.11, clipforge/briefs/advice.py): a niche worth an account, in US$ through
// your exchange rates, with the accounts to make, three names, a bio, a picture idea, the posting plan and
// the paused clipping channel Set it up starts. ``make`` is empty once an account is tagged for it.
export type Suggestion = {
  niche: string;
  language: string;
  label: string;
  count: number; // its live briefs
  budget_left: number | null;
  per_day: number;
  as_if: boolean; // some of it had no exchange rate
  guess: boolean;
  best: {
    id: string;
    title: string;
    brand: string | null;
    worth: BriefWorth;
  }[];
  make: Platform[];
  names: string[];
  bio: string;
  picture: string;
  channel: { name: string; sources: string[]; account_ids: string[] };
  plan: string;
};

export type Advice = { suggestions: Suggestion[]; could_take: string[] };
export type AdviceBody = { advice: Advice };

// Use this campaign: the New campaign form filled from the brief, and whether its page was opened.
export type UseResult = ParseResult & { brief_id: string; opened: boolean };

// Today (SPEC 14.10): the day's list, its 15 minutes and the board around it.
export type TodoKind =
  | "drafts"
  | "needs_you"
  | "login"
  | "insights"
  | "submit"
  | "views"
  | "decide"
  | "ends"
  | "payout"
  | "exported"
  | "briefs"
  | "source"
  | "join"
  | "path";

export type TodoItem = {
  id: string;
  kind: TodoKind;
  ref: string; // the channel, campaign, account, platform or marketplace it is about
  title: string;
  detail: string | null;
  estimate_s: number;
  value: number; // what it could earn, unblock or has at stake, in its currency
  currency: string;
  as_if_usd: boolean; // its currency has no rate in Settings, so it was compared as if US$
  guess: boolean;
  optional: boolean;
  due: boolean;
  carried_over: boolean;
  done: boolean;
  done_how: "app" | "hand" | null;
  campaigns: Record<string, number>; // how many of its things each campaign has
};

export type PostToPutUp = {
  post_id: string;
  status: "exported" | "scheduled_on_tiktok";
  handle: string | null; // "@name", or the account's label without a handle
  platform: Platform;
  hook: string | null;
  caption: string;
  hashtags: string[];
  full_caption: string; // as it goes up, hashtags and all
  scheduled_at: number;
  has_file: boolean; // its exported clip.mp4 is still there
};

export type SubmitEntry = Submission & {
  handle: string | null;
  platform: Platform;
  hook: string | null;
  url: string | null; // the live post
  campaign_name: string;
  marketplace: string;
  currency: string;
  submit_link: string | null; // the id Open sends, never the link
  todo: ("submit" | "decide" | "views")[];
};

export type Guideline = {
  id: string;
  name: string;
  marketplace: string;
  currency: string;
  rate: number | null; // per 1,000 views
  minimum: number | null; // views
  cap: number | null; // a clip's most
  hashtags: string[];
  mentions: string[];
  text: string[];
  banned: Banned[];
  platforms: Platform[];
  ends_on: string | null;
  budget_left: number | null;
  budget_entered_on: string | null;
  page_link: string | null;
};

export type AccountToday = {
  id: string;
  handle: string | null;
  platform: Platform;
  today: Account["today"]; // as its card has it
};

export type BestPost = {
  post_id: string;
  hook: string | null;
  handle: string | null;
  platform: Platform;
  views: number;
  gained: number; // since the newest count at least 20 hours before
  url: string | null;
};

export type TodayBody = {
  day: string; // "2026-11-03", in the posting time zone
  budget_s: number;
  used_s: number;
  left_s: number; // what the undone items that aren't optional need
  built: boolean; // the morning has made the day's list
  items: TodoItem[];
  post: PostToPutUp[];
  submit: SubmitEntry[];
  guidelines: Guideline[];
  accounts: AccountToday[];
  money: MoneyLine[];
  best: BestPost[];
  path: PathView;
};

// The path from nothing to your first payout (SPEC 14.10): six levels of steps, each done once ClipForge
// sees it done or you tick it. "todo" is on the list as its own item, "listed" is on it through another
// item, "waiting" waits on a marketplace, "locked" waits on the steps before it.
export type PathState = "done" | "todo" | "listed" | "waiting" | "locked";

export type PathStep = {
  id: string;
  title: string;
  detail: string;
  state: PathState;
  done_how: "app" | "hand" | null;
  to: string | null; // the page its work is on, null for one done where you are
};

export type PathLevel = {
  n: number;
  name: string;
  goal: string;
  steps: PathStep[];
};

export type PathAccount = {
  broad: boolean; // spread over many niches, else one niche's
  niche: string | null;
  names: string[];
  bio: string;
  picture: string;
  tags: string[];
  per_day: number; // what it could earn a day at the starting guess
  currency: string;
};

export type PathOn = {
  on: true;
  level: number | null; // null once every step is done
  done: number;
  total: number;
  levels: PathLevel[];
  metrics: {
    accounts: number;
    posts: number;
    earned: number;
    currency: string;
  };
  account: PathAccount;
};

export type PathView = { on: false } | PathOn;
