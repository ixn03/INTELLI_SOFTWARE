from app.models.control_model import ExplanationResult, TraceResult


def explain_trace(trace: TraceResult) -> ExplanationResult:
    if trace.status != "answered":
        explanation = trace.summary
    elif not trace.causes:
        explanation = (
            f"{trace.target_tag} has a driving instruction, but the prototype did not find any "
            "parsed permissive or interlock conditions feeding that instruction."
        )
    else:
        unique_conditions = sorted({cause.tag for cause in trace.causes})
        condition_text = ", ".join(unique_conditions)

        explanation = (
            f"{trace.target_tag} is controlled by parsed logic in the normalized INTELLI model. "
            f"For this output to be true, these upstream conditions must allow the rung or block "
            f"to pass: {condition_text}. If the output is false, start by checking those condition "
            "tags in the listed routines."
        )

    return ExplanationResult(
        target_tag=trace.target_tag,
        explanation=explanation,
        trace=trace,
    )
