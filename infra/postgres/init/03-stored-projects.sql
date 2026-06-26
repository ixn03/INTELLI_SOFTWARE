-- INTELLI stored control projects and normalized reasoning graphs.
-- Persists parsed ControlProject blobs and optional normalized graph caches.

CREATE TABLE IF NOT EXISTS stored_projects (
    file_hash TEXT PRIMARY KEY,
    project_name TEXT,
    source_file TEXT,
    connector TEXT,
    project_blob JSONB NOT NULL,
    normalized_blob JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_stored_projects_updated_at ON stored_projects (updated_at);
