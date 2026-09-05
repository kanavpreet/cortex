"""matik-api Phase 1c service-signature check metrics.

Mirrors ChroniclerMetrics.record_signature — same idea (HMAC check outcome
as a labeled counter), applied to the application-layer service-identity
check on matik-api instead of Chronicler's inbound webhooks. See
api/main.py::create_service_signature_dispatch.

Alerting rules (document as comments for SRE reference):

  # Real callers producing invalid signatures once enforcement is on —
  # likely a caller missing/out of sync with the shared secret.
  ALERT ApiServiceSignatureInvalidWhileEnforced
    IF rate(matik_api_service_signature_checks_total{result="invalid",enforced="true"}[5m]) > 0

  # Use this one to decide when shadow mode is clean enough to flip
  # enforce_service_signature: true for an environment.
  ALERT ApiServiceSignatureInvalidInShadowMode
    IF rate(matik_api_service_signature_checks_total{result="invalid",enforced="false"}[15m]) > 0
"""

from typing import Any

from opentelemetry import metrics


class ServiceSignatureMetrics:
    """Metrics instrument for the Phase 1c service-signature check.

    Usage::

        metrics = ServiceSignatureMetrics(meter, "api")
        metrics.record(valid=True, reason="", enforced=False)
    """

    def __init__(self, meter: metrics.Meter, service_name: str = "api") -> None:
        self._service_name = service_name

        self._checks = meter.create_counter(
            name="matik_api_service_signature_checks_total",
            description=(
                "Phase 1c service-to-service signature check outcomes on "
                "matik-api, labeled by result (valid, invalid), reason "
                "(empty when valid; missing_headers, malformed_timestamp, "
                "timestamp_out_of_window, or signature_mismatch when "
                "invalid), and enforced (whether the check was rejecting "
                "requests or just logging in shadow mode)."
            ),
            unit="{check}",
        )

    def record(self, *, valid: bool, reason: str, enforced: bool) -> None:
        """Record one signature-check outcome.

        Args:
            valid: Whether the signature verified.
            reason: SignatureVerificationError message, or "" when valid.
            enforced: Whether this check was in enforce mode (rejecting) or
                shadow mode (logging only).
        """
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "result": "valid" if valid else "invalid",
            "reason": reason,
            "enforced": "true" if enforced else "false",
        }
        self._checks.add(1, attrs)
