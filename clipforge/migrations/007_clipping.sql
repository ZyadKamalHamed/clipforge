-- ClipForge schema version 7, for clipping channels (SPEC 10.9, 15): a clipping channel watches some
-- YouTube channels (its sources) and fills some accounts; each daily check is a row, each video it saw is an
-- item, and the drafts it made are ordinary posts with the status draft. Deleting a clipping channel takes
-- its sources, items and checks with it and leaves its projects as ordinary ones.

CREATE TABLE clip_channels (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    account_ids_json TEXT NOT NULL DEFAULT '[]',
    options_json TEXT NOT NULL DEFAULT '{}',
    rights_basis TEXT NOT NULL DEFAULT 'not_cleared',
    check_time TEXT NOT NULL DEFAULT '06:00',
    daily_video_limit INTEGER NOT NULL DEFAULT 3,
    clips_per_day INTEGER,
    on_limit TEXT NOT NULL DEFAULT 'wait',
    paused INTEGER NOT NULL DEFAULT 0,
    last_check_on TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE watched_sources (
    id TEXT PRIMARY KEY,
    clip_channel_id TEXT NOT NULL REFERENCES clip_channels (id) ON DELETE CASCADE,
    yt_channel_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    avatar_path TEXT,
    start_published_at REAL NOT NULL,
    newest_video_id TEXT,
    newest_title TEXT,
    newest_published_at REAL,
    last_feed_at REAL,
    error TEXT,
    created_at REAL NOT NULL,
    UNIQUE (clip_channel_id, yt_channel_id)
);

CREATE TABLE watch_checks (
    id TEXT PRIMARY KEY,
    clip_channel_id TEXT NOT NULL REFERENCES clip_channels (id) ON DELETE CASCADE,
    check_on TEXT NOT NULL,
    manual INTEGER NOT NULL DEFAULT 0,
    started_at REAL NOT NULL,
    finished_at REAL,
    found INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    clipped INTEGER NOT NULL DEFAULT 0,
    drafts INTEGER,
    error TEXT,
    drafted_at REAL
);

CREATE INDEX watch_checks_channel ON watch_checks (clip_channel_id, started_at);

CREATE TABLE watch_items (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES watched_sources (id) ON DELETE CASCADE,
    clip_channel_id TEXT NOT NULL REFERENCES clip_channels (id) ON DELETE CASCADE,
    check_id TEXT,
    video_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    published_at REAL,
    status TEXT NOT NULL CHECK (status IN ('new', 'skipped', 'waiting', 'clipping', 'drafted', 'failed')),
    reason TEXT,
    project_id TEXT REFERENCES projects (id) ON DELETE SET NULL,
    first_seen_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (clip_channel_id, video_id)
);

CREATE INDEX watch_items_check ON watch_items (check_id);

ALTER TABLE projects ADD COLUMN clip_channel_id TEXT REFERENCES clip_channels (id) ON DELETE SET NULL;
