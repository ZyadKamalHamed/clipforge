-- ClipForge schema version 11, for trends and sounds (SPEC 8.6, 12, 13, 15): sounds Zyad shortlisted from
-- TikTok and the trending audio Instagram lists for each account linked through Facebook (titles and ids
-- only, never the sound itself), hashtag sets per niche, and when each list was last read, so Instagram is
-- asked once a day per account and SocialPilot's feed once a week. Music in the library keeps whether its
-- licence asks for credit and how much energy it has; its moods go in tags_json.

CREATE TABLE sounds (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL CHECK (platform IN ('tiktok', 'instagram')),
    source TEXT NOT NULL CHECK (source IN ('shortlist', 'ig_trending')),
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    artist TEXT,
    url TEXT,
    region TEXT CHECK (region IN ('AU', 'US')),
    mood_tags_json TEXT NOT NULL DEFAULT '[]',
    energy INTEGER CHECK (energy BETWEEN 1 AND 5),
    audio_type TEXT CHECK (audio_type IN ('music', 'original_sound')),
    duration_s REAL,
    rank INTEGER,
    account_id TEXT REFERENCES accounts (id) ON DELETE CASCADE,
    fetched_at REAL,
    created_at REAL NOT NULL,
    CHECK ((source = 'ig_trending') = (account_id IS NOT NULL AND audio_type IS NOT NULL))
);
CREATE UNIQUE INDEX sounds_shortlisted ON sounds (external_id) WHERE source = 'shortlist';
CREATE UNIQUE INDEX sounds_trending ON sounds (account_id, audio_type, external_id) WHERE source = 'ig_trending';

CREATE TABLE hashtag_sets (
    id TEXT PRIMARY KEY,
    niche TEXT NOT NULL,
    name TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE trend_reads (
    id TEXT PRIMARY KEY,  -- ig:<account id> or feed:socialpilot
    read_at REAL,
    newest_at REAL,
    seen_at REAL,
    result TEXT CHECK (result IN ('ok', 'error', 'blocked')),
    error TEXT
);

ALTER TABLE library_items ADD COLUMN credit_required INTEGER NOT NULL DEFAULT 0 CHECK (credit_required IN (0, 1));
ALTER TABLE library_items ADD COLUMN energy INTEGER CHECK (energy BETWEEN 1 AND 5);
