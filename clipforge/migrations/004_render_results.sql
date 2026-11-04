-- ClipForge schema version 4: why a clip failed, in a sentence, and when a render's cover frame shows.

ALTER TABLE clips ADD COLUMN error TEXT;
ALTER TABLE renders ADD COLUMN cover_s REAL;
