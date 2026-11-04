-- ClipForge schema version 8, for paid clipping campaigns (SPEC 10.10, 15): a campaign profile holds a
-- marketplace campaign's rules and links; clipping channels and projects link to one; every live post of a
-- linked clip has a submission Zyad moves along; and a post's view counts, read from Instagram's insights or
-- entered by hand, are rows of their own. Deleting a campaign leaves its channels and projects unlinked.

CREATE TABLE campaigns (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    marketplace TEXT NOT NULL,
    campaign_url TEXT,
    submit_url TEXT,
    currency TEXT NOT NULL DEFAULT 'USD',
    rate_per_1000 REAL,
    min_views INTEGER,
    max_payout REAL,
    platforms_json TEXT NOT NULL DEFAULT '["tiktok", "instagram"]',
    footage_json TEXT NOT NULL DEFAULT '{}',
    required_json TEXT NOT NULL DEFAULT '{}',
    banned_json TEXT NOT NULL DEFAULT '{}',
    min_s REAL,
    max_s REAL,
    ends_on TEXT,
    budget_left REAL,
    budget_entered_on TEXT,
    notes TEXT NOT NULL DEFAULT '',
    rules_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended')),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE submissions (
    id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL UNIQUE REFERENCES posts (id) ON DELETE CASCADE,
    campaign_id TEXT NOT NULL REFERENCES campaigns (id),
    status TEXT NOT NULL CHECK (status IN ('to_submit', 'submitted', 'approved', 'rejected', 'paid')),
    submitted_at REAL,
    decided_at REAL,
    reason TEXT,
    paid_amount REAL,
    paid_on TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX submissions_campaign ON submissions (campaign_id, status);

CREATE TABLE post_metrics (
    post_id TEXT NOT NULL REFERENCES posts (id) ON DELETE CASCADE,
    captured_at REAL NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('insights', 'hand')),
    views INTEGER NOT NULL,
    likes INTEGER,
    comments INTEGER,
    shares INTEGER,
    saves INTEGER
);

CREATE INDEX post_metrics_post ON post_metrics (post_id, captured_at);

ALTER TABLE projects ADD COLUMN campaign_id TEXT REFERENCES campaigns (id) ON DELETE SET NULL;
ALTER TABLE clip_channels ADD COLUMN campaign_id TEXT REFERENCES campaigns (id) ON DELETE SET NULL;
