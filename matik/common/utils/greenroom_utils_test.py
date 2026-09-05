"""Tests for greenroom utility functions."""

import os
import tempfile

import yaml

from common.utils.greenroom_utils import (
    load_jira_backstage_mapping,
    resolve_jira_services_from_mapping,
)


class TestLoadJiraBackstageMapping:
    """Tests for load_jira_backstage_mapping function."""

    def test_load_valid_mapping(self) -> None:
        """Test loading a valid YAML mapping file."""
        data = {
            "AWS S3": {
                "match_count": 1,
                "matches": [
                    {
                        "backstage_name": "biztech_aws-s3",
                        "backstage_title": "biztech_aws-s3",
                        "score": 100,
                    }
                ],
            },
            "Airflow": {
                "match_count": 2,
                "matches": [
                    {
                        "backstage_name": "airflow",
                        "backstage_title": "Airflow",
                        "score": 100,
                    },
                    {
                        "backstage_name": "airflow-worker",
                        "backstage_title": "Airflow Worker",
                        "score": 90,
                    },
                ],
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(data, f)
            path = f.name

        try:
            mapping = load_jira_backstage_mapping(path)

            assert "AWS S3" in mapping
            assert mapping["AWS S3"] == ["biztech_aws-s3"]
            assert "Airflow" in mapping
            assert mapping["Airflow"] == ["airflow", "airflow-worker"]
        finally:
            os.unlink(path)

    def test_skip_zero_match_count(self) -> None:
        """Test that entries with match_count 0 are skipped."""
        data = {
            "AWS S3": {
                "match_count": 1,
                "matches": [{"backstage_name": "biztech_aws-s3", "score": 100}],
            },
            "Unknown Service": {
                "match_count": 0,
                "matches": [],
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(data, f)
            path = f.name

        try:
            mapping = load_jira_backstage_mapping(path)

            assert "AWS S3" in mapping
            assert "Unknown Service" not in mapping
        finally:
            os.unlink(path)

    def test_file_not_found(self) -> None:
        """Test that missing file returns empty dict."""
        mapping = load_jira_backstage_mapping("/nonexistent/path.yml")
        assert mapping == {}

    def test_empty_file(self) -> None:
        """Test that empty YAML file returns empty dict."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("")
            path = f.name

        try:
            mapping = load_jira_backstage_mapping(path)
            assert mapping == {}
        finally:
            os.unlink(path)

    def test_invalid_yaml_returns_empty(self) -> None:
        """Test that invalid YAML returns empty dict."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(":\n  :\n  - [invalid")
            path = f.name

        try:
            mapping = load_jira_backstage_mapping(path)
            assert mapping == {}
        finally:
            os.unlink(path)

    def test_non_dict_entry_skipped(self) -> None:
        """Test that non-dict entries are skipped."""
        data = {
            "ValidService": {
                "match_count": 1,
                "matches": [{"backstage_name": "valid-svc", "score": 100}],
            },
            "BadEntry": "not a dict",
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(data, f)
            path = f.name

        try:
            mapping = load_jira_backstage_mapping(path)
            assert "ValidService" in mapping
            assert "BadEntry" not in mapping
        finally:
            os.unlink(path)

    def test_non_list_matches_skipped(self) -> None:
        """Test that entries with non-list matches are skipped."""
        data = {
            "BadMatches": {
                "match_count": 1,
                "matches": "not a list",
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(data, f)
            path = f.name

        try:
            mapping = load_jira_backstage_mapping(path)
            assert "BadMatches" not in mapping
        finally:
            os.unlink(path)

    def test_skip_entries_without_backstage_name(self) -> None:
        """Test that matches without backstage_name are skipped."""
        data = {
            "Service A": {
                "match_count": 1,
                "matches": [{"backstage_title": "no-name-field", "score": 50}],
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(data, f)
            path = f.name

        try:
            mapping = load_jira_backstage_mapping(path)
            # Entry has match_count=1 but no backstage_name, so not included
            assert "Service A" not in mapping
        finally:
            os.unlink(path)


class TestResolveJiraServicesFromMapping:
    """Tests for resolve_jira_services_from_mapping function."""

    def test_resolve_single_service(self) -> None:
        """Test resolving a single service name."""
        mapping = {"AWS S3": ["biztech_aws-s3"]}
        result = resolve_jira_services_from_mapping(["AWS S3"], mapping)
        assert result == ["biztech_aws-s3"]

    def test_resolve_multiple_services(self) -> None:
        """Test resolving multiple service names."""
        mapping = {
            "AWS S3": ["biztech_aws-s3"],
            "Airflow": ["airflow", "airflow-worker"],
        }
        result = resolve_jira_services_from_mapping(["AWS S3", "Airflow"], mapping)
        assert result == ["biztech_aws-s3", "airflow", "airflow-worker"]

    def test_deduplicate_results(self) -> None:
        """Test that duplicate backstage names are deduplicated."""
        mapping = {
            "Service A": ["shared-service"],
            "Service B": ["shared-service", "unique-service"],
        }
        result = resolve_jira_services_from_mapping(["Service A", "Service B"], mapping)
        assert result == ["shared-service", "unique-service"]

    def test_no_matching_services(self) -> None:
        """Test resolving services with no matches returns empty list."""
        mapping = {"AWS S3": ["biztech_aws-s3"]}
        result = resolve_jira_services_from_mapping(["Unknown Service"], mapping)
        assert result == []

    def test_empty_tcmr_services(self) -> None:
        """Test with empty tcmr_services list."""
        mapping = {"AWS S3": ["biztech_aws-s3"]}
        result = resolve_jira_services_from_mapping([], mapping)
        assert result == []

    def test_empty_mapping(self) -> None:
        """Test with empty mapping."""
        result = resolve_jira_services_from_mapping(["AWS S3"], {})
        assert result == []

    def test_partial_matches(self) -> None:
        """Test with mix of matching and non-matching services."""
        mapping = {"AWS S3": ["biztech_aws-s3"]}
        result = resolve_jira_services_from_mapping(["AWS S3", "Unknown"], mapping)
        assert result == ["biztech_aws-s3"]
