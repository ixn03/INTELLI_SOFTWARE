import networkx as nx

from app.models.control_model import ControlInstruction, ControlProject


def build_logic_graph(project: ControlProject) -> nx.DiGraph:
    graph = nx.DiGraph()

    for controller in project.controllers:
        for tag in controller.controller_tags:
            graph.add_node(tag.name, kind="tag", scope=tag.scope, data_type=tag.data_type)

        for program in controller.programs:
            for tag in program.tags:
                graph.add_node(tag.name, kind="tag", scope=tag.scope, data_type=tag.data_type)

            for routine in program.routines:
                for instruction in routine.instructions:
                    instruction_id = _instruction_node_id(
                        controller.name,
                        program.name,
                        routine.name,
                        instruction,
                    )

                    graph.add_node(
                        instruction_id,
                        kind="instruction",
                        instruction_type=instruction.instruction_type,
                        routine=routine.name,
                        program=program.name,
                        raw_text=instruction.raw_text,
                    )

                    for operand in instruction.operands:
                        graph.add_node(operand, kind="tag")
                        if operand == instruction.output:
                            graph.add_edge(instruction_id, operand, relationship="drives")
                        else:
                            graph.add_edge(operand, instruction_id, relationship="conditions")

    return graph


def graph_summary(project: ControlProject) -> dict[str, int]:
    graph = build_logic_graph(project)

    return {
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "tags": sum(1 for _, data in graph.nodes(data=True) if data.get("kind") == "tag"),
        "instructions": sum(
            1 for _, data in graph.nodes(data=True) if data.get("kind") == "instruction"
        ),
    }


def _instruction_node_id(
    controller: str,
    program: str,
    routine: str,
    instruction: ControlInstruction,
) -> str:
    suffix = instruction.id or f"{instruction.instruction_type}:{','.join(instruction.operands)}"
    return f"{controller}/{program}/{routine}/{suffix}"
