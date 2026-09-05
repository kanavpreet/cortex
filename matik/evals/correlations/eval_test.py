"""Test that the incident correlation eval's main() wires run_eval correctly."""

from unittest.mock import patch

from evals.correlations.incident import eval as incident_eval


def test_main_calls_run_eval_with_category_dataset_name() -> None:
    with patch.object(incident_eval, "run_eval") as run_eval:
        incident_eval.main()
    run_eval.assert_called_once()
    kwargs = run_eval.call_args.kwargs
    assert kwargs["eval_slug"] == "incident"
    assert kwargs["dataset_name"] == "correlation-incident-golden"
