-- ClipForge schema version 9, for the Daily dashboard (SPEC 14.10, 15): the day's to-do list, one row per
-- day, kind and what it is about, kept in step with what needs Zyad; how long each kind of item took, so
-- tomorrow's estimates are his own; and each day's plan, with the drafts cap that keeps it inside 15 minutes.
-- Phase 4's switch for notifications as they happen moves to Today's settings, where it is off by default
-- (SPEC 18), so the stored posting value drops it.

CREATE TABLE todo_items (
    id TEXT PRIMARY KEY,
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    ref TEXT NOT NULL,
    ref_json TEXT NOT NULL,
    title TEXT NOT NULL,
    estimate_s INTEGER NOT NULL CHECK (estimate_s >= 0),
    value REAL NOT NULL,
    optional INTEGER NOT NULL DEFAULT 0 CHECK (optional IN (0, 1)),
    carried_from TEXT,
    done_at REAL,
    done_how TEXT CHECK (done_how IN ('app', 'hand')),
    created_at REAL NOT NULL,
    UNIQUE (day, kind, ref)
);

CREATE TABLE item_timings (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL NOT NULL,
    seconds REAL NOT NULL CHECK (seconds >= 0),
    units INTEGER NOT NULL CHECK (units >= 1)
);
CREATE INDEX item_timings_kind ON item_timings (kind, finished_at);

CREATE TABLE days (
    day TEXT PRIMARY KEY,
    drafts_cap INTEGER,
    planned_s INTEGER,
    used_s INTEGER NOT NULL DEFAULT 0,
    notified_at REAL
);

UPDATE settings SET value_json = json_remove(value_json, '$.notify_events')
WHERE key = 'posting' AND json_valid(value_json);
