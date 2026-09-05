from typing import Any

from langgraph.graph import END, StateGraph

from .nodes import (
    assign_correlations_by_llm,
    assign_correlations_by_service,
    fetch_biztech_github_prs,
    fetch_jira_issues,
    route_after_fetch,
)
from .scoring import (
    compute_correlation_group,
    score_llm_correlations,
    score_service_correlations,
)
from .state import IncidentCorrelationState


def build_graph() -> Any:
    # Create a graph that operates on CorrelationState
    graph = StateGraph(IncidentCorrelationState)

    # Node: Each node is a pure function that takes state and returns state
    # Entry Point: The entry point is the node where execution starts when the graph is invoked.
    # Edge: An edge is an unconditional transition that says "after this node finishes, always run that node next.

    # Find Jira Issues
    graph.add_node("fetch_jira_issues", fetch_jira_issues)

    # Find Github Prs
    graph.add_node("fetch_biztech_github_prs", fetch_biztech_github_prs)

    # Get correlation suggestions from Facade
    graph.add_node("assign_correlations_by_llm", assign_correlations_by_llm)

    # Assign correlations when service exists
    graph.add_node("assign_correlations_by_service", assign_correlations_by_service)

    # Score service correlations by temporal proximity
    graph.add_node("score_service_correlations", score_service_correlations)

    # Score LLM correlations by applying the LLM cap
    graph.add_node("score_llm_correlations", score_llm_correlations)

    # Merge all correlations into a single group with an overall score
    graph.add_node("compute_correlation_group", compute_correlation_group)

    # --- Entry point ---
    graph.set_entry_point("fetch_jira_issues")

    # --- Normal flow ---
    graph.add_edge("fetch_jira_issues", "fetch_biztech_github_prs")

    # --- Conditional: end early when no change events; else service vs. LLM ---
    # With no events there is nothing to correlate, so skip straight to END —
    # both the service and LLM branches (and the grouping that follows) would be
    # no-ops, leaving correlation_group unset either way.
    graph.add_conditional_edges(
        "fetch_biztech_github_prs",
        route_after_fetch,
        {
            "skip": END,
            "service": "assign_correlations_by_service",
            "llm": "assign_correlations_by_llm",
        },
    )

    # Service path: assign → score → LLM → score LLM → group
    graph.add_edge("assign_correlations_by_service", "score_service_correlations")
    graph.add_edge("score_service_correlations", "assign_correlations_by_llm")

    # LLM path: assign → score LLM → group
    graph.add_edge("assign_correlations_by_llm", "score_llm_correlations")
    graph.add_edge("score_llm_correlations", "compute_correlation_group")
    graph.add_edge("compute_correlation_group", END)

    # Compile the graph into an executable object
    return graph.compile()
