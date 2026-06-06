-- INTELLI metadata schema (MVP stub for projects + tag registry)

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tag_registry (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects (id) ON DELETE CASCADE,
    tag_id TEXT NOT NULL,
    raw_tag_name TEXT NOT NULL,
    normalized_name TEXT,
    data_type TEXT,
    equipment_id TEXT,
    area TEXT,
    source TEXT NOT NULL DEFAULT 'OPC_UA',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_tag_registry_project ON tag_registry (project_id);
CREATE INDEX IF NOT EXISTS idx_tag_registry_raw_name ON tag_registry (raw_tag_name);
