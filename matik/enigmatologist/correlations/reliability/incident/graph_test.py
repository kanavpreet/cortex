"""Tests for the incident correlation LangGraph builder."""

from enigmatologist.correlations.reliability.incident.graph import build_graph


class TestBuildGraph:
    """Tests for build_graph function."""

    def test_returns_compiled_graph(self) -> None:
        """build_graph returns a compiled LangGraph object."""
        graph = build_graph()
        assert graph is not None

    def test_graph_has_all_nodes(self) -> None:
        """Graph contains all expected nodes."""
        graph = build_graph()
        node_names = set(graph.get_graph().nodes.keys())
        expected = {
            "__start__",
            "__end__",
            "fetch_jira_issues",
            "fetch_biztech_github_prs",
            "assign_correlations_by_llm",
            "assign_correlations_by_service",
            "score_service_correlations",
            "score_llm_correlations",
            "compute_correlation_group",
        }
        assert expected.issubset(node_names)

    def test_entry_point_is_fetch_jira(self) -> None:
        """Graph starts at fetch_jira_issues."""
        graph = build_graph()
        draw = graph.get_graph()
        # __start__ should have an edge to fetch_jira_issues
        start_edges = [e.target for e in draw.edges if e.source == "__start__"]
        assert "fetch_jira_issues" in start_edges

    def test_end_node_after_compute_group(self) -> None:
        """compute_correlation_group leads to __end__."""
        graph = build_graph()
        draw = graph.get_graph()
        group_edges = [
            e.target for e in draw.edges if e.source == "compute_correlation_group"
        ]
        assert "__end__" in group_edges

    def test_jira_to_github_edge(self) -> None:
        """fetch_jira_issues flows to fetch_biztech_github_prs."""
        graph = build_graph()
        draw = graph.get_graph()
        edges = [e.target for e in draw.edges if e.source == "fetch_jira_issues"]
        assert "fetch_biztech_github_prs" in edges

    def test_conditional_edge_from_github(self) -> None:
        """fetch_biztech_github_prs has edges to both service and LLM paths."""
        graph = build_graph()
        draw = graph.get_graph()
        edges = [e.target for e in draw.edges if e.source == "fetch_biztech_github_prs"]
        assert "assign_correlations_by_service" in edges
        assert "assign_correlations_by_llm" in edges

    def test_skip_edge_from_github_to_end(self) -> None:
        """fetch_biztech_github_prs can skip straight to __end__ when no events."""
        graph = build_graph()
        draw = graph.get_graph()
        edges = [e.target for e in draw.edges if e.source == "fetch_biztech_github_prs"]
        assert "__end__" in edges

    def test_service_path_flows_through_scoring(self) -> None:
        """assign_correlations_by_service -> score_service_correlations -> assign_correlations_by_llm."""
        graph = build_graph()
        draw = graph.get_graph()

        svc_edges = [
            e.target for e in draw.edges if e.source == "assign_correlations_by_service"
        ]
        assert "score_service_correlations" in svc_edges

        score_svc_edges = [
            e.target for e in draw.edges if e.source == "score_service_correlations"
        ]
        assert "assign_correlations_by_llm" in score_svc_edges

    def test_llm_path_flows_through_scoring(self) -> None:
        """assign_correlations_by_llm -> score_llm_correlations -> compute_correlation_group."""
        graph = build_graph()
        draw = graph.get_graph()

        llm_edges = [
            e.target for e in draw.edges if e.source == "assign_correlations_by_llm"
        ]
        assert "score_llm_correlations" in llm_edges

        score_llm_edges = [
            e.target for e in draw.edges if e.source == "score_llm_correlations"
        ]
        assert "compute_correlation_group" in score_llm_edges

    def test_graph_is_deterministic(self) -> None:
        """Two calls to build_graph produce graphs with the same structure."""
        g1 = build_graph()
        g2 = build_graph()
        nodes1 = set(g1.get_graph().nodes.keys())
        nodes2 = set(g2.get_graph().nodes.keys())
        assert nodes1 == nodes2
