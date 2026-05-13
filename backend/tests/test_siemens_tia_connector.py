from app.connectors.registry import get_connector
from app.connectors.siemens_tia import SiemensTIAConnector
from app.services.normalization_service import normalize_l5x_project


SIEMENS_XML = b"""<?xml version="1.0"?>
<DocumentInfo>
  <ProjectName>Line 7</ProjectName>
  <SW.Blocks.OB>
    <AttributeList>
      <Name>MainOB</Name>
      <ProgrammingLanguage>LAD</ProgrammingLanguage>
    </AttributeList>
    <Interface>
      <Section Name="Input">
        <Member Name="StartPB" Datatype="Bool" />
      </Section>
    </Interface>
    <ObjectList>
      <FlgNet><Network><AttributeList><Name>Network 1</Name></AttributeList></Network></FlgNet>
    </ObjectList>
  </SW.Blocks.OB>
  <SW.Blocks.FB>
    <AttributeList>
      <Name>MotorFB</Name>
      <ProgrammingLanguage>GRAPH</ProgrammingLanguage>
    </AttributeList>
  </SW.Blocks.FB>
</DocumentInfo>
"""


def test_siemens_xml_detection() -> None:
    connector = get_connector("export.xml", SIEMENS_XML)
    assert isinstance(connector, SiemensTIAConnector)


def test_siemens_block_routine_and_interface_extraction() -> None:
    project = SiemensTIAConnector().parse("export.xml", SIEMENS_XML)
    program = project.controllers[0].programs[0]
    routines = {routine.name: routine for routine in program.routines}
    assert routines["MainOB"].language == "tia_ob"
    assert routines["MainOB"].metadata["block_type"] == "OB"
    assert routines["MainOB"].metadata["block_language"] == "LAD"
    assert routines["MainOB"].raw_logic
    assert any(tag.name == "StartPB" and tag.data_type == "Bool" for tag in program.tags)


def test_siemens_unsupported_language_preserved_in_graph() -> None:
    project = SiemensTIAConnector().parse("export.xml", SIEMENS_XML)
    graph = normalize_l5x_project(project)
    motor = next(obj for obj in graph["control_objects"] if obj.name == "MotorFB")
    assert motor.platform_specific["platform"] == "siemens_tia"
    assert motor.platform_specific["block_language"] == "GRAPH"
    assert motor.platform_specific["raw_xml_present"] is True


def test_siemens_unknown_xml_does_not_crash() -> None:
    project = SiemensTIAConnector().parse("unknown.xml", b"<?xml version='1.0'?><Root />")
    assert project.project_name == "Siemens TIA Project"
    assert project.controllers[0].programs[0].routines[0].parse_status == "preserved_only"
