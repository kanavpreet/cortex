"""Unit tests for JiraConfig model."""

import pytest
from pydantic import ValidationError

from common.models.jira_config import JiraConfig


class TestJiraConfig:
    """Test suite for JiraConfig Pydantic model."""

    def test_valid_minimal(self) -> None:
        """Test creating JiraConfig with minimal required fields."""
        config = JiraConfig(
            base_url="https://jira.example.com",
            username="service_account",
            password="secret123",
        )
        assert config.base_url == "https://jira.example.com"
        assert config.username == "service_account"
        assert config.password == "secret123"
        assert config.tcmr_jql_enabled is False  # Default
        assert config.tcmr_jql is None
        assert config.operational_jql_enabled is False  # Default
        assert config.operational_jql is None
        assert config.pagination_max_results == 200  # Default
        assert config.lookback_days == 30  # Default
        assert config.llm_description_prompt is None
        assert config.llm_comments_prompt is None
        assert config.client_timeout == 60.0  # Default

    def test_valid_full(self) -> None:
        """Test creating JiraConfig with all fields."""
        config = JiraConfig(
            base_url="https://jira.corp.com/rest/api/2",
            username="matik-service",
            password="super-secret",
            tcmr_jql_enabled=True,
            tcmr_jql="project = TCMR AND status = Open",
            operational_jql_enabled=True,
            operational_jql="project = OPS AND type = Incident",
            pagination_max_results=100,
            lookback_days=90,
            llm_description_prompt="Summarize this issue",
            llm_comments_prompt="Extract action items",
            client_timeout=120.0,
        )
        assert config.base_url == "https://jira.corp.com/rest/api/2"
        assert config.tcmr_jql_enabled is True
        assert config.tcmr_jql == "project = TCMR AND status = Open"
        assert config.operational_jql_enabled is True
        assert config.operational_jql == "project = OPS AND type = Incident"
        assert config.pagination_max_results == 100
        assert config.lookback_days == 90
        assert config.llm_description_prompt == "Summarize this issue"
        assert config.llm_comments_prompt == "Extract action items"
        assert config.client_timeout == 120.0

    def test_required_fields_missing(self) -> None:
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            JiraConfig()
        error_str = str(exc_info.value)
        assert "base_url" in error_str
        assert "username" in error_str
        assert "password" in error_str

    def test_model_validate(self) -> None:
        """Test creating model from dict."""
        data = {
            "base_url": "https://jira.test.com",
            "username": "test_user",
            "password": "test_pass",
            "pagination_max_results": 50,
        }
        config = JiraConfig.model_validate(data)
        assert config.base_url == "https://jira.test.com"
        assert config.pagination_max_results == 50

    def test_model_dump(self) -> None:
        """Test serializing model to dict."""
        config = JiraConfig(
            base_url="https://jira.example.com",
            username="user",
            password="pass",
            lookback_days=60,
        )
        data = config.model_dump()
        assert data["base_url"] == "https://jira.example.com"
        assert data["lookback_days"] == 60
        assert data["pagination_max_results"] == 200

    def test_default_pagination(self) -> None:
        """Test default pagination settings."""
        config = JiraConfig(
            base_url="https://jira.example.com",
            username="user",
            password="pass",
        )
        assert config.pagination_max_results == 200
        assert config.lookback_days == 30
