-- SQLite schema for the NAF Orchestrator durable audit log.
-- Append-only by API contract; no UPDATE or DELETE paths are exposed.

CREATE TABLE IF NOT EXISTS audit_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      TEXT NOT NULL UNIQUE,
    workflow_id   TEXT NOT NULL,
    workflow_type TEXT NOT NULL,
    target_id     TEXT,
    event_type    TEXT NOT NULL,
    activity_name TEXT,
    outcome       TEXT,
    reason        TEXT,
    payload_json  TEXT NOT NULL,
    timestamp     TEXT NOT NULL,
    actor         TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_workflow_id ON audit_events(workflow_id);
CREATE INDEX IF NOT EXISTS idx_audit_target_id   ON audit_events(target_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp   ON audit_events(timestamp);
