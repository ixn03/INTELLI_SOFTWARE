from app.models.control_model import ControlProject, TraceCause, TraceResult
from app.services.graph_service import build_logic_graph


def trace_tag(project: ControlProject, target_tag: str, question: str = "why_false") -> TraceResult:
    graph = build_logic_graph(project)

    if target_tag not in graph:
        return TraceResult(
            target_tag=target_tag,
            question=question,
            status="not_found",
            summary=f"{target_tag} was not found in the normalized project model.",
        )

    drivers = [
        predecessor
        for predecessor in graph.predecessors(target_tag)
        if graph.edges[predecessor, target_tag].get("relationship") == "drives"
    ]

    if not drivers:
        return TraceResult(
            target_tag=target_tag,
            question=question,
            status="unsupported",
            summary=f"No output instruction driving {target_tag} was found in the normalized logic graph.",
            evidence={"graph_node": graph.nodes[target_tag]},
        )

    causes: list[TraceCause] = []

    for driver in drivers:
        driver_data = graph.nodes[driver]

        for condition in graph.predecessors(driver):
            edge = graph.edges[condition, driver]
            if edge.get("relationship") != "conditions":
                continue

            causes.append(
                TraceCause(
                    tag=condition,
                    relationship="conditions_output",
                    instruction_type=driver_data.get("instruction_type"),
                    routine=driver_data.get("routine"),
                    program=driver_data.get("program"),
                    raw_text=driver_data.get("raw_text"),
                )
            )

    return TraceResult(
        target_tag=target_tag,
        question=question,
        status="answered",
        summary=(
            f"{target_tag} is driven by {len(drivers)} instruction(s) and depends on "
            f"{len(causes)} upstream condition tag(s)."
        ),
        causes=causes,
        evidence={"driver_instruction_count": len(drivers)},
    )
