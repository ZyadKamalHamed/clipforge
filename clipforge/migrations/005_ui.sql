-- ClipForge schema version 5, for the window: when a clip was set aside, which edits a render was made
-- from, and the technical details that sit behind a failure's plain sentence.

ALTER TABLE clips ADD COLUMN discarded_at REAL;
ALTER TABLE renders ADD COLUMN edits_key TEXT;
ALTER TABLE jobs ADD COLUMN error_details TEXT;
ALTER TABLE projects ADD COLUMN error_details TEXT;
