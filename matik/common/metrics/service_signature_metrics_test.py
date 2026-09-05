"""Unit tests for ServiceSignatureMetrics."""

from unittest.mock import MagicMock

from common.metrics.service_signature_metrics import ServiceSignatureMetrics


def _make_meter() -> MagicMock:
    meter = MagicMock()
    meter.create_counter.return_value = MagicMock()
    return meter


class TestServiceSignatureMetricsInit:
    def test_creates_expected_instrument(self) -> None:
        meter = _make_meter()
        ServiceSignatureMetrics(meter)
        assert meter.create_counter.call_count == 1

    def test_counter_name(self) -> None:
        meter = _make_meter()
        ServiceSignatureMetrics(meter)
        name = meter.create_counter.call_args[1]["name"]
        assert name == "matik_api_service_signature_checks_total"


class TestServiceSignatureMetricsEmission:
    def _setup(self) -> ServiceSignatureMetrics:
        return ServiceSignatureMetrics(_make_meter(), "api")

    def test_record_valid(self) -> None:
        m = self._setup()
        m.record(valid=True, reason="", enforced=True)
        m._checks.add.assert_called_once_with(  # type: ignore[attr-defined]
            1,
            {
                "service": "api",
                "result": "valid",
                "reason": "",
                "enforced": "true",
            },
        )

    def test_record_invalid_in_shadow_mode(self) -> None:
        m = self._setup()
        m.record(valid=False, reason="signature_mismatch", enforced=False)
        m._checks.add.assert_called_once_with(  # type: ignore[attr-defined]
            1,
            {
                "service": "api",
                "result": "invalid",
                "reason": "signature_mismatch",
                "enforced": "false",
            },
        )

    def test_record_invalid_reasons_all_emit(self) -> None:
        m = self._setup()
        for reason in (
            "missing_headers",
            "malformed_timestamp",
            "timestamp_out_of_window",
            "signature_mismatch",
        ):
            m.record(valid=False, reason=reason, enforced=True)
        assert m._checks.add.call_count == 4  # type: ignore[attr-defined]
