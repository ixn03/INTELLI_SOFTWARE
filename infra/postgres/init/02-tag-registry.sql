-- INTELLI Tag Registry (Step 2)
-- Adds plants, areas, equipment, data sources, connector configs, tags, aliases, logic refs.
-- Keeps legacy tables from 01-init.sql intact.

CREATE TABLE IF NOT EXISTS plants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS areas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plant_id UUID NOT NULL REFERENCES plants (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (plant_id, name)
);

CREATE TABLE IF NOT EXISTS equipment (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plant_id UUID NOT NULL REFERENCES plants (id) ON DELETE CASCADE,
    area_id UUID NOT NULL REFERENCES areas (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    equipment_type TEXT,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (plant_id, name)
);

CREATE TABLE IF NOT EXISTS data_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plant_id UUID NOT NULL REFERENCES plants (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    source_system TEXT NOT NULL CHECK (
        source_system IN ('OPC_UA', 'PI', 'DELTAV', 'SEEQ', 'CSV', 'SIMULATOR')
    ),
    endpoint_url TEXT,
    auth_type TEXT,
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (plant_id, name)
);

CREATE INDEX IF NOT EXISTS ix_data_sources_source_system ON data_sources (source_system);

CREATE TABLE IF NOT EXISTS connector_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id UUID NOT NULL REFERENCES data_sources (id) ON DELETE CASCADE,
    config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    polling_interval_ms INTEGER,
    subscription_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tags (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plant_id UUID NOT NULL REFERENCES plants (id) ON DELETE CASCADE,
    area_id UUID NOT NULL REFERENCES areas (id) ON DELETE CASCADE,
    equipment_id UUID REFERENCES equipment (id) ON DELETE SET NULL,
    data_source_id UUID NOT NULL REFERENCES data_sources (id) ON DELETE CASCADE,
    raw_name TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    description TEXT,
    data_type TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK (
        data_type IN ('BOOL', 'INT', 'DINT', 'REAL', 'STRING', 'UNKNOWN')
    ),
    tag_role TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK (
        tag_role IN (
            'COMMAND', 'FEEDBACK', 'PERMISSIVE', 'INTERLOCK', 'ALARM',
            'ANALOG', 'STATUS', 'SETPOINT', 'UNKNOWN'
        )
    ),
    unit TEXT,
    source_system TEXT NOT NULL CHECK (
        source_system IN ('OPC_UA', 'PI', 'DELTAV', 'SEEQ', 'CSV', 'SIMULATOR')
    ),
    source_address TEXT,
    node_id TEXT,
    scan_rate_ms INTEGER,
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (plant_id, canonical_name)
);

CREATE INDEX IF NOT EXISTS ix_tags_raw_name ON tags (raw_name);
CREATE INDEX IF NOT EXISTS ix_tags_equipment_id ON tags (equipment_id);
CREATE INDEX IF NOT EXISTS ix_tags_data_source_id ON tags (data_source_id);
CREATE INDEX IF NOT EXISTS ix_tags_source_system ON tags (source_system);

CREATE TABLE IF NOT EXISTS tag_aliases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tag_id UUID NOT NULL REFERENCES tags (id) ON DELETE CASCADE,
    alias_name TEXT NOT NULL,
    source_system TEXT CHECK (
        source_system IS NULL OR source_system IN (
            'OPC_UA', 'PI', 'DELTAV', 'SEEQ', 'CSV', 'SIMULATOR'
        )
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tag_id, alias_name)
);

CREATE TABLE IF NOT EXISTS tag_logic_refs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tag_id UUID NOT NULL REFERENCES tags (id) ON DELETE CASCADE,
    reference_type TEXT NOT NULL,
    reference_key TEXT,
    program_name TEXT,
    routine_name TEXT,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Demo seed (idempotent)
INSERT INTO plants (id, name, description)
VALUES (
    '11111111-1111-1111-1111-111111111101',
    'Demo Plant',
    'INTELLI demo plant for tag registry MVP'
)
ON CONFLICT (name) DO NOTHING;

INSERT INTO areas (id, plant_id, name, description)
VALUES (
    '11111111-1111-1111-1111-111111111102',
    '11111111-1111-1111-1111-111111111101',
    'Utilities',
    'Utilities area'
)
ON CONFLICT (plant_id, name) DO NOTHING;

INSERT INTO equipment (id, plant_id, area_id, name, equipment_type, description)
VALUES
    (
        '11111111-1111-1111-1111-111111111103',
        '11111111-1111-1111-1111-111111111101',
        '11111111-1111-1111-1111-111111111102',
        'P101 Pump',
        'PUMP',
        'Transfer pump P101'
    ),
    (
        '11111111-1111-1111-1111-111111111104',
        '11111111-1111-1111-1111-111111111101',
        '11111111-1111-1111-1111-111111111102',
        'XV101 Valve',
        'VALVE',
        'Block valve XV101'
    ),
    (
        '11111111-1111-1111-1111-111111111105',
        '11111111-1111-1111-1111-111111111101',
        '11111111-1111-1111-1111-111111111102',
        'Tank101',
        'TANK',
        'Storage tank Tank101'
    )
ON CONFLICT (plant_id, name) DO NOTHING;

INSERT INTO data_sources (id, plant_id, name, source_system, endpoint_url, auth_type, is_enabled)
VALUES (
    '11111111-1111-1111-1111-111111111106',
    '11111111-1111-1111-1111-111111111101',
    'Prosys OPC UA Simulation',
    'OPC_UA',
    'opc.tcp://host.docker.internal:53530/OPCUA/SimulationServer',
    'NONE',
    TRUE
)
ON CONFLICT (plant_id, name) DO NOTHING;

INSERT INTO connector_configs (id, data_source_id, config_json, polling_interval_ms, subscription_enabled)
VALUES (
    '11111111-1111-1111-1111-111111111107',
    '11111111-1111-1111-1111-111111111106',
    '{"namespace": 3, "security_mode": "None"}'::jsonb,
    1000,
    TRUE
)
ON CONFLICT DO NOTHING;

INSERT INTO tags (
    id, plant_id, area_id, equipment_id, data_source_id,
    raw_name, canonical_name, description, data_type, tag_role,
    unit, source_system, source_address, node_id, scan_rate_ms
)
VALUES
    (
        '11111111-1111-1111-1111-111111111201',
        '11111111-1111-1111-1111-111111111101',
        '11111111-1111-1111-1111-111111111102',
        '11111111-1111-1111-1111-111111111103',
        '11111111-1111-1111-1111-111111111106',
        'P101_RunCmd',
        'P101_RunCmd',
        'Command to start pump P101',
        'BOOL', 'COMMAND', NULL, 'OPC_UA',
        'ns=3;i=1009', 'ns=3;i=1009', 500
    ),
    (
        '11111111-1111-1111-1111-111111111202',
        '11111111-1111-1111-1111-111111111101',
        '11111111-1111-1111-1111-111111111102',
        '11111111-1111-1111-1111-111111111103',
        '11111111-1111-1111-1111-111111111106',
        'P101_RunningFB',
        'P101_RunningFB',
        'Confirms pump P101 is actually running',
        'BOOL', 'FEEDBACK', NULL, 'OPC_UA',
        'ns=3;i=1010', 'ns=3;i=1010', 500
    ),
    (
        '11111111-1111-1111-1111-111111111203',
        '11111111-1111-1111-1111-111111111101',
        '11111111-1111-1111-1111-111111111102',
        '11111111-1111-1111-1111-111111111103',
        '11111111-1111-1111-1111-111111111106',
        'P101_Fault',
        'P101_Fault',
        'Pump P101 fault indicator',
        'BOOL', 'ALARM', NULL, 'OPC_UA',
        'ns=3;i=1011', 'ns=3;i=1011', 1000
    ),
    (
        '11111111-1111-1111-1111-111111111204',
        '11111111-1111-1111-1111-111111111101',
        '11111111-1111-1111-1111-111111111102',
        '11111111-1111-1111-1111-111111111104',
        '11111111-1111-1111-1111-111111111106',
        'XV101_OpenCmd',
        'XV101_OpenCmd',
        'Command to open valve XV101',
        'BOOL', 'COMMAND', NULL, 'OPC_UA',
        'ns=3;i=1012', 'ns=3;i=1012', 500
    ),
    (
        '11111111-1111-1111-1111-111111111205',
        '11111111-1111-1111-1111-111111111101',
        '11111111-1111-1111-1111-111111111102',
        '11111111-1111-1111-1111-111111111104',
        '11111111-1111-1111-1111-111111111106',
        'XV101_OpenFB',
        'XV101_OpenFB',
        'Confirms valve XV101 is open',
        'BOOL', 'FEEDBACK', NULL, 'OPC_UA',
        'ns=3;i=1013', 'ns=3;i=1013', 500
    ),
    (
        '11111111-1111-1111-1111-111111111206',
        '11111111-1111-1111-1111-111111111101',
        '11111111-1111-1111-1111-111111111102',
        '11111111-1111-1111-1111-111111111105',
        '11111111-1111-1111-1111-111111111106',
        'Tank101_Level',
        'Tank101_Level',
        'Tank101 level process variable',
        'REAL', 'ANALOG', '%', 'OPC_UA',
        'ns=3;i=1014', 'ns=3;i=1014', 1000
    )
ON CONFLICT (plant_id, canonical_name) DO NOTHING;
