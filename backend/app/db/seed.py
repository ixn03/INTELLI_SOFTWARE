"""Demo seed data for the INTELLI tag registry."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.db.models.tag_registry import (
    Area,
    ConnectorConfig,
    DataSource,
    Equipment,
    Plant,
    SourceSystem,
    Tag,
    TagDataType,
    TagRole,
)

DEMO_PLANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111101")
DEMO_AREA_ID = uuid.UUID("11111111-1111-1111-1111-111111111102")
DEMO_EQUIP_P101_ID = uuid.UUID("11111111-1111-1111-1111-111111111103")
DEMO_EQUIP_XV101_ID = uuid.UUID("11111111-1111-1111-1111-111111111104")
DEMO_EQUIP_TANK101_ID = uuid.UUID("11111111-1111-1111-1111-111111111105")
DEMO_DATA_SOURCE_ID = uuid.UUID("11111111-1111-1111-1111-111111111106")
DEMO_TAG_P101_RUNCMD_ID = uuid.UUID("11111111-1111-1111-1111-111111111201")
DEMO_TAG_P101_RUNNINGFB_ID = uuid.UUID("11111111-1111-1111-1111-111111111202")
DEMO_TAG_TANK101_LEVEL_ID = uuid.UUID("11111111-1111-1111-1111-111111111206")

# Prosys OPC UA Simulation Server — namespace index may differ per install (default ns=3).
PROSYS_OPCUA_ENDPOINT = "opc.tcp://host.docker.internal:53530/OPCUA/SimulationServer"


def seed_demo_registry(db: Session) -> None:
    """Insert demo plant/equipment/tags if not already present."""

    if db.get(Plant, DEMO_PLANT_ID) is not None:
        return

    plant = Plant(
        id=DEMO_PLANT_ID,
        name="Demo Plant",
        description="INTELLI demo plant for tag registry MVP",
    )
    area = Area(
        id=DEMO_AREA_ID,
        plant_id=DEMO_PLANT_ID,
        name="Utilities",
        description="Utilities area",
    )
    equip_p101 = Equipment(
        id=DEMO_EQUIP_P101_ID,
        plant_id=DEMO_PLANT_ID,
        area_id=DEMO_AREA_ID,
        name="P101 Pump",
        equipment_type="PUMP",
        description="Transfer pump P101",
    )
    equip_xv101 = Equipment(
        id=DEMO_EQUIP_XV101_ID,
        plant_id=DEMO_PLANT_ID,
        area_id=DEMO_AREA_ID,
        name="XV101 Valve",
        equipment_type="VALVE",
        description="Block valve XV101",
    )
    equip_tank = Equipment(
        id=DEMO_EQUIP_TANK101_ID,
        plant_id=DEMO_PLANT_ID,
        area_id=DEMO_AREA_ID,
        name="Tank101",
        equipment_type="TANK",
        description="Storage tank Tank101",
    )
    data_source = DataSource(
        id=DEMO_DATA_SOURCE_ID,
        plant_id=DEMO_PLANT_ID,
        name="Prosys OPC UA Simulation",
        source_system=SourceSystem.OPC_UA,
        endpoint_url=PROSYS_OPCUA_ENDPOINT,
        auth_type="NONE",
        is_enabled=True,
    )
    connector = ConnectorConfig(
        data_source_id=DEMO_DATA_SOURCE_ID,
        config_json={"namespace": 3, "security_mode": "None"},
        polling_interval_ms=1000,
        subscription_enabled=True,
    )

    tags = [
        Tag(
            id=uuid.UUID("11111111-1111-1111-1111-111111111201"),
            plant_id=DEMO_PLANT_ID,
            area_id=DEMO_AREA_ID,
            equipment_id=DEMO_EQUIP_P101_ID,
            data_source_id=DEMO_DATA_SOURCE_ID,
            raw_name="P101_RunCmd",
            canonical_name="P101_RunCmd",
            description="Command to start pump P101",
            data_type=TagDataType.BOOL,
            tag_role=TagRole.COMMAND,
            source_system=SourceSystem.OPC_UA,
            source_address="ns=3;i=1009",
            node_id="ns=3;i=1009",
            scan_rate_ms=500,
        ),
        Tag(
            id=uuid.UUID("11111111-1111-1111-1111-111111111202"),
            plant_id=DEMO_PLANT_ID,
            area_id=DEMO_AREA_ID,
            equipment_id=DEMO_EQUIP_P101_ID,
            data_source_id=DEMO_DATA_SOURCE_ID,
            raw_name="P101_RunningFB",
            canonical_name="P101_RunningFB",
            description="Confirms pump P101 is actually running",
            data_type=TagDataType.BOOL,
            tag_role=TagRole.FEEDBACK,
            source_system=SourceSystem.OPC_UA,
            source_address="ns=3;i=1010",
            node_id="ns=3;i=1010",
            scan_rate_ms=500,
        ),
        Tag(
            id=uuid.UUID("11111111-1111-1111-1111-111111111203"),
            plant_id=DEMO_PLANT_ID,
            area_id=DEMO_AREA_ID,
            equipment_id=DEMO_EQUIP_P101_ID,
            data_source_id=DEMO_DATA_SOURCE_ID,
            raw_name="P101_Fault",
            canonical_name="P101_Fault",
            description="Pump P101 fault indicator",
            data_type=TagDataType.BOOL,
            tag_role=TagRole.ALARM,
            source_system=SourceSystem.OPC_UA,
            source_address="ns=3;i=1011",
            node_id="ns=3;i=1011",
            scan_rate_ms=1000,
        ),
        Tag(
            id=uuid.UUID("11111111-1111-1111-1111-111111111204"),
            plant_id=DEMO_PLANT_ID,
            area_id=DEMO_AREA_ID,
            equipment_id=DEMO_EQUIP_XV101_ID,
            data_source_id=DEMO_DATA_SOURCE_ID,
            raw_name="XV101_OpenCmd",
            canonical_name="XV101_OpenCmd",
            description="Command to open valve XV101",
            data_type=TagDataType.BOOL,
            tag_role=TagRole.COMMAND,
            source_system=SourceSystem.OPC_UA,
            source_address="ns=3;i=1012",
            node_id="ns=3;i=1012",
            scan_rate_ms=500,
        ),
        Tag(
            id=uuid.UUID("11111111-1111-1111-1111-111111111205"),
            plant_id=DEMO_PLANT_ID,
            area_id=DEMO_AREA_ID,
            equipment_id=DEMO_EQUIP_XV101_ID,
            data_source_id=DEMO_DATA_SOURCE_ID,
            raw_name="XV101_OpenFB",
            canonical_name="XV101_OpenFB",
            description="Confirms valve XV101 is open",
            data_type=TagDataType.BOOL,
            tag_role=TagRole.FEEDBACK,
            source_system=SourceSystem.OPC_UA,
            source_address="ns=3;i=1013",
            node_id="ns=3;i=1013",
            scan_rate_ms=500,
        ),
        Tag(
            id=uuid.UUID("11111111-1111-1111-1111-111111111206"),
            plant_id=DEMO_PLANT_ID,
            area_id=DEMO_AREA_ID,
            equipment_id=DEMO_EQUIP_TANK101_ID,
            data_source_id=DEMO_DATA_SOURCE_ID,
            raw_name="Tank101_Level",
            canonical_name="Tank101_Level",
            description="Tank101 level process variable",
            data_type=TagDataType.REAL,
            tag_role=TagRole.ANALOG,
            unit="%",
            source_system=SourceSystem.OPC_UA,
            source_address="ns=3;i=1014",
            node_id="ns=3;i=1014",
            scan_rate_ms=1000,
        ),
    ]

    db.add_all(
        [
            plant,
            area,
            equip_p101,
            equip_xv101,
            equip_tank,
            data_source,
            connector,
            *tags,
        ]
    )
    db.flush()
