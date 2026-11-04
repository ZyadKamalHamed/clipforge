-- ClipForge schema version 6, for posting (SPEC 10, 15): the linked accounts, each Confirm and post as a
-- batch, and one row per clip and account. Tokens are never stored here, only the Keychain item that
-- holds them. A post keeps its clip's title and place in the video after the clip is deleted.

CREATE TABLE accounts (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL CHECK (platform IN ('tiktok', 'instagram')),
    method TEXT NOT NULL CHECK (method IN ('browser', 'api')),
    label TEXT NOT NULL DEFAULT '',
    handle TEXT,
    display_name TEXT,
    avatar_path TEXT,
    status TEXT NOT NULL DEFAULT 'needs_login',
    paused INTEGER NOT NULL DEFAULT 0,
    status_note TEXT,
    profile_dir TEXT,
    ig_mode TEXT,
    ig_user_id TEXT,
    fb_page_id TEXT,
    token_ref TEXT,
    token_expires_at REAL,
    token_refreshed_at REAL,
    settings_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    last_checked_at REAL,
    deleted_at REAL
);

CREATE UNIQUE INDEX accounts_live_handle ON accounts (platform, handle)
    WHERE deleted_at IS NULL AND handle IS NOT NULL;

CREATE TABLE batches (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL,
    schedule_mode TEXT NOT NULL,
    acknowledgements_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);

CREATE TABLE posts (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES batches (id),
    clip_id TEXT REFERENCES clips (id) ON DELETE SET NULL,
    clip_title TEXT NOT NULL DEFAULT '',
    render_id TEXT REFERENCES renders (id) ON DELETE SET NULL,
    account_id TEXT NOT NULL REFERENCES accounts (id),
    platform TEXT NOT NULL,
    method TEXT NOT NULL,
    caption TEXT NOT NULL DEFAULT '',
    hashtags_json TEXT NOT NULL DEFAULT '[]',
    options_json TEXT NOT NULL DEFAULT '{}',
    scheduled_at REAL NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    external_id TEXT,
    url TEXT,
    error TEXT,
    error_code TEXT,
    details TEXT,
    note TEXT,
    state_json TEXT NOT NULL DEFAULT '{}',
    log_path TEXT,
    posted_at REAL,
    video_key TEXT NOT NULL,
    start_s REAL NOT NULL,
    end_s REAL NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX posts_status_time ON posts (status, scheduled_at);
CREATE INDEX posts_account_time ON posts (account_id, scheduled_at);

ALTER TABLE clips ADD COLUMN topic_tags_json TEXT;
