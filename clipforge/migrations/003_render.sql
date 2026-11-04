CREATE TABLE renders (
    id TEXT PRIMARY KEY,
    clip_id TEXT NOT NULL REFERENCES clips (id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    style_key TEXT NOT NULL,
    path TEXT NOT NULL,
    cover_path TEXT,
    thumb_path TEXT,
    duration_s REAL,
    size_bytes INTEGER,
    edl_path TEXT,
    qa_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE (clip_id, version)
);
CREATE INDEX renders_clip ON renders (clip_id);

CREATE TABLE library_items (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    source_url TEXT,
    credit TEXT,
    license TEXT,
    license_url TEXT,
    notes TEXT,
    duration_s REAL,
    width INTEGER,
    height INTEGER,
    tags_json TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL
);
CREATE INDEX library_items_kind ON library_items (kind);

CREATE TABLE gameplay_uses (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES library_items (id) ON DELETE CASCADE,
    start_s REAL NOT NULL,
    end_s REAL NOT NULL,
    clip_id TEXT REFERENCES clips (id) ON DELETE SET NULL,
    account_id TEXT,
    post_id TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX gameplay_uses_item ON gameplay_uses (item_id);
