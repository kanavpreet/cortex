"""Unit tests for the Phase 1c service-to-service HMAC signing helper."""

import time

import pytest

from common.utils.service_auth import (
    SignatureVerificationError,
    build_signature_headers,
    sign,
    verify_request,
)

_SECRET = "test-shared-secret"


class TestSign:
    """Test suite for sign()."""

    def test_deterministic_for_fixed_timestamp(self) -> None:
        """Same inputs + explicit timestamp always produce the same signature."""
        ts_a, sig_a = sign(
            _SECRET, "POST", "/v1/mcp/incidentio", b"{}", timestamp="100"
        )
        ts_b, sig_b = sign(
            _SECRET, "POST", "/v1/mcp/incidentio", b"{}", timestamp="100"
        )
        assert ts_a == ts_b == "100"
        assert sig_a == sig_b

    def test_defaults_timestamp_to_now(self) -> None:
        """With no explicit timestamp, sign() uses the current time."""
        before = int(time.time())
        ts, _ = sign(_SECRET, "GET", "/health", b"")
        after = int(time.time())
        assert before <= int(ts) <= after

    def test_signature_is_prefixed_hex_digest(self) -> None:
        _, sig = sign(_SECRET, "GET", "/v1/mcp/incidentio", b"", timestamp="100")
        assert sig.startswith("sha256=")
        assert len(sig) == len("sha256=") + 64  # SHA-256 hex digest length

    @pytest.mark.parametrize(
        ("method", "path", "body"),
        [
            ("GET", "/v1/mcp/incidentio", b"{}"),  # different method
            ("POST", "/v1/mcp/jira", b"{}"),  # different path
            ("POST", "/v1/mcp/incidentio", b'{"a":1}'),  # different body
        ],
    )
    def test_changing_any_input_changes_signature(
        self, method: str, path: str, body: bytes
    ) -> None:
        _, baseline = sign(
            _SECRET, "POST", "/v1/mcp/incidentio", b"{}", timestamp="100"
        )
        _, variant = sign(_SECRET, method, path, body, timestamp="100")
        assert variant != baseline


def test_known_vector_matches_scripts_tester() -> None:
    """Fixed input -> fixed hex digest, kept identical to a matching test in
    scripts/service_signature_tester/tester_test.py.

    scripts/ and integration-tests/ intentionally reimplement this HMAC
    construction rather than importing this module (see their own
    docstrings), so a change here can't fail their test suites directly.
    This vector is the tripwire: if this construction ever changes, this
    test's expected digest must be updated by hand, which is the prompt to
    go update the matching hardcoded digest in tester_test.py too.
    """
    _, signature = sign(
        _SECRET,
        "POST",
        "/v1/mcp/incidentio?foo=bar",
        b'{"a":1}',
        timestamp="1700000000",
    )
    assert (
        signature
        == "sha256=0dadceac05c381038b192edf1bc99a9ca1adb29c92b4ad7b1c0e70146fc4fc1b"
    )


class TestBuildSignatureHeaders:
    """Test suite for build_signature_headers()."""

    def test_returns_both_headers(self) -> None:
        headers = build_signature_headers(_SECRET, "POST", "/v1/mcp/incidentio", b"{}")
        assert set(headers) == {
            "X-Matik-Service-Timestamp",
            "X-Matik-Service-Signature",
        }
        assert headers["X-Matik-Service-Signature"].startswith("sha256=")


class TestVerifyRequest:
    """Test suite for verify_request()."""

    def test_valid_signature_passes(self) -> None:
        headers = build_signature_headers(_SECRET, "POST", "/v1/mcp/incidentio", b"{}")
        verify_request(
            _SECRET,
            "POST",
            "/v1/mcp/incidentio",
            b"{}",
            headers["X-Matik-Service-Timestamp"],
            headers["X-Matik-Service-Signature"],
        )  # does not raise

    def test_wrong_secret_rejected(self) -> None:
        headers = build_signature_headers(_SECRET, "POST", "/v1/mcp/incidentio", b"{}")
        with pytest.raises(SignatureVerificationError, match="signature_mismatch"):
            verify_request(
                "a-different-secret",
                "POST",
                "/v1/mcp/incidentio",
                b"{}",
                headers["X-Matik-Service-Timestamp"],
                headers["X-Matik-Service-Signature"],
            )

    def test_tampered_body_rejected(self) -> None:
        headers = build_signature_headers(_SECRET, "POST", "/v1/mcp/incidentio", b"{}")
        with pytest.raises(SignatureVerificationError, match="signature_mismatch"):
            verify_request(
                _SECRET,
                "POST",
                "/v1/mcp/incidentio",
                b'{"tampered": true}',
                headers["X-Matik-Service-Timestamp"],
                headers["X-Matik-Service-Signature"],
            )

    def test_tampered_path_rejected(self) -> None:
        """A signature captured for one route can't be replayed against another."""
        headers = build_signature_headers(_SECRET, "POST", "/v1/mcp/incidentio", b"{}")
        with pytest.raises(SignatureVerificationError, match="signature_mismatch"):
            verify_request(
                _SECRET,
                "POST",
                "/v1/mcp/jira",
                b"{}",
                headers["X-Matik-Service-Timestamp"],
                headers["X-Matik-Service-Signature"],
            )

    @pytest.mark.parametrize(
        ("timestamp_header", "signature_header"),
        [
            (None, "sha256=deadbeef"),
            ("100", None),
            (None, None),
            ("", "sha256=deadbeef"),
            ("100", ""),
        ],
    )
    def test_missing_headers_rejected(
        self, timestamp_header: str | None, signature_header: str | None
    ) -> None:
        with pytest.raises(SignatureVerificationError, match="missing_headers"):
            verify_request(
                _SECRET,
                "POST",
                "/v1/mcp/incidentio",
                b"{}",
                timestamp_header,
                signature_header,
            )

    def test_malformed_timestamp_rejected(self) -> None:
        with pytest.raises(SignatureVerificationError, match="malformed_timestamp"):
            verify_request(
                _SECRET,
                "POST",
                "/v1/mcp/incidentio",
                b"{}",
                "not-a-number",
                "sha256=deadbeef",
            )

    def test_stale_timestamp_rejected_even_with_correct_signature(self) -> None:
        """A validly-signed but old request is still replay-protected out."""
        stale_timestamp = str(int(time.time()) - 1000)
        _, signature = sign(
            _SECRET, "POST", "/v1/mcp/incidentio", b"{}", timestamp=stale_timestamp
        )
        with pytest.raises(SignatureVerificationError, match="timestamp_out_of_window"):
            verify_request(
                _SECRET,
                "POST",
                "/v1/mcp/incidentio",
                b"{}",
                stale_timestamp,
                signature,
                max_skew_seconds=120,
            )

    def test_future_timestamp_rejected(self) -> None:
        """Skew check is symmetric — a timestamp too far in the future also fails."""
        future_timestamp = str(int(time.time()) + 1000)
        _, signature = sign(
            _SECRET, "POST", "/v1/mcp/incidentio", b"{}", timestamp=future_timestamp
        )
        with pytest.raises(SignatureVerificationError, match="timestamp_out_of_window"):
            verify_request(
                _SECRET,
                "POST",
                "/v1/mcp/incidentio",
                b"{}",
                future_timestamp,
                signature,
                max_skew_seconds=120,
            )

    def test_timestamp_within_configured_skew_passes(self) -> None:
        recent_timestamp = str(int(time.time()) - 30)
        _, signature = sign(
            _SECRET, "POST", "/v1/mcp/incidentio", b"{}", timestamp=recent_timestamp
        )
        verify_request(
            _SECRET,
            "POST",
            "/v1/mcp/incidentio",
            b"{}",
            recent_timestamp,
            signature,
            max_skew_seconds=120,
        )  # does not raise
