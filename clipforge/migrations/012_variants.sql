-- ClipForge schema version 12, for each account's own version of a clip (Phase 5g, SPEC 11): a render made
-- for one account's posts, in its style kit or on a stretch of gameplay no other account's post has shown,
-- names that account. The clip's own renders keep it NULL, and only they ever become the clip's current one.
ALTER TABLE renders ADD COLUMN account_id TEXT;
