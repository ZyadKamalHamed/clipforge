-- ClipForge schema version 10, for campaign briefs and account advice (SPEC 10.11, 15): where briefs come
-- from (HeyClicky's Campaign Scout file first; Whop's Content Rewards and Vyro are paste-only because their
-- terms forbid automated reading, research 08), the briefs found, advice Zyad hid for 30 days, and each
-- site's pages read per day, so the 5 second pacing and the 40 page cap hold across runs. A campaign keeps
-- the brief it came from and when Zyad joined it on the marketplace.

CREATE TABLE brief_sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    marketplace TEXT NOT NULL,
    list_url TEXT,
    reader TEXT NOT NULL CHECK (reader IN ('campaign_scout', 'general', 'paste')),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    terms_checked_on TEXT,
    terms_note TEXT,
    last_run_at REAL,
    last_result TEXT CHECK (last_result IN ('ok', 'blocked', 'sign_in', 'changed', 'error')),
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

INSERT INTO brief_sources (id, name, marketplace, list_url, reader, enabled, terms_checked_on, terms_note,
                           created_at, updated_at)
VALUES
    ('bs_scout', 'Campaign Scout (HeyClicky)', 'Whop and Vyro', NULL, 'campaign_scout', 1, NULL,
     'Reads HeyClicky''s Campaign Scout file on this Mac. It makes no request to any site.',
     CAST(strftime('%s', 'now') AS REAL), CAST(strftime('%s', 'now') AS REAL)),
    ('bs_content_rewards', 'Whop Content Rewards', 'Whop', 'https://contentrewards.com/discover', 'paste', 1,
     '2026-10-29',
     'Whop''s terms forbid automated reading, so paste its briefs. HeyClicky''s Campaign Scout file already lists its campaigns.',
     CAST(strftime('%s', 'now') AS REAL) + 0.001, CAST(strftime('%s', 'now') AS REAL) + 0.001),
    ('bs_vyro', 'Vyro', 'Vyro', 'https://vyro.com/campaigns', 'paste', 1, '2026-10-29',
     'Vyro''s terms forbid automated reading, so paste its briefs.',
     CAST(strftime('%s', 'now') AS REAL) + 0.002, CAST(strftime('%s', 'now') AS REAL) + 0.002);

CREATE TABLE briefs (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT,
    marketplace TEXT NOT NULL,
    brand TEXT,
    title TEXT NOT NULL,
    niche TEXT,
    language TEXT,
    currency TEXT NOT NULL DEFAULT 'USD',
    rate_per_1000 REAL,
    rates_json TEXT NOT NULL DEFAULT '{}',
    min_views INTEGER,
    max_payout REAL,
    caps_json TEXT NOT NULL DEFAULT '{}',
    budget_total REAL,
    budget_spent REAL,
    budget_left REAL,
    budget_read_at REAL,
    ends_on TEXT,
    platforms_json TEXT NOT NULL,
    footage_json TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    fields_json TEXT NOT NULL DEFAULT '{}',
    notes_json TEXT NOT NULL DEFAULT '{}',
    text_hash TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'seen', 'hidden', 'used', 'gone')),
    campaign_id TEXT,
    first_seen_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    UNIQUE (source_id, external_id)
);
CREATE UNIQUE INDEX briefs_url ON briefs (url) WHERE url IS NOT NULL;
CREATE INDEX briefs_status ON briefs (status);

CREATE TABLE advice_hidden (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('suggestion', 'niche')),
    niche TEXT NOT NULL,
    language TEXT NOT NULL,
    until TEXT NOT NULL
);

CREATE TABLE site_reads (
    site TEXT NOT NULL,
    day TEXT NOT NULL,
    pages INTEGER NOT NULL DEFAULT 0,
    last_at REAL,
    PRIMARY KEY (site, day)
);

ALTER TABLE campaigns ADD COLUMN brief_id TEXT;
ALTER TABLE campaigns ADD COLUMN joined_at REAL;
