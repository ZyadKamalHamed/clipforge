-- ClipForge schema version 2: the hook titles Discover suggests for each clip idea, as a JSON list.

ALTER TABLE clips ADD COLUMN hook_titles_json TEXT NOT NULL DEFAULT '[]';
