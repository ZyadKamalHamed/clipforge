-- ClipForge schema version 1: projects, clips, jobs and settings (SPEC 6.3, 7).
-- Later phases add their own tables in their own migrations.

CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    source_url TEXT,
    video_id TEXT,
    title TEXT,
    channel TEXT,
    channel_url TEXT,
    duration_s REAL,
    language TEXT,
    license TEXT,
    rights_basis TEXT NOT NULL DEFAULT 'not_cleared',
    focus_s REAL,
    prompt TEXT,
    options_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT,
    progress REAL NOT NULL DEFAULT 0,
    message TEXT,
    mode TEXT,
    error_code TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX projects_video_id ON projects (video_id);

CREATE TABLE clips (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    rank INTEGER,
    start_s REAL NOT NULL,
    end_s REAL NOT NULL,
    sentence_ids TEXT NOT NULL,
    scores_json TEXT,
    llm_score REAL,
    rerank_score REAL,
    signal_score REAL,
    final_score REAL,
    tier TEXT,
    passes_gates INTEGER,
    archetype TEXT,
    reason TEXT,
    evidence_json TEXT,
    flags_json TEXT,
    packaging_json TEXT,
    review_json TEXT,
    edits_json TEXT,
    layout TEXT,
    layout_reason TEXT,
    mode TEXT,
    status TEXT NOT NULL DEFAULT 'idea',
    current_render_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX clips_project_rank ON clips (project_id, rank);

CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lane TEXT NOT NULL,
    kind TEXT NOT NULL,
    ref_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 0,
    progress REAL NOT NULL DEFAULT 0,
    message TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    error_code TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 1,
    not_before REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    heartbeat_at REAL
);
CREATE INDEX jobs_claim ON jobs (status, lane, priority, created_at);
CREATE INDEX jobs_ref ON jobs (ref_id, kind);

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
