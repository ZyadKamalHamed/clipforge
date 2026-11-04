-- ClipForge schema version 13, for the path on Today (SPEC 14.10): each step from nothing to your first
-- payout once it is done, seen done by ClipForge or ticked by you. A step done stays done, whatever changes
-- after, so the path never goes back. Make your first account keeps the account it was planned as, so
-- its name ideas and tags stay put while the briefs change.
CREATE TABLE path_steps (
    step TEXT PRIMARY KEY,
    done_at REAL NOT NULL,
    done_how TEXT NOT NULL CHECK (done_how IN ('app', 'hand')),
    plan_json TEXT
);
