"""Unit tests for environment variable validation logic."""

import tempfile
from pathlib import Path

import pytest

from .validator import (
    extract_env_variables,
    parse_env_example,
    scan_local_configs,
)


class TestExtractEnvVariables:
    """Test suite for extract_env_variables function."""

    def test_single_variable(self) -> None:
        """Test extracting single variable."""
        result = extract_env_variables("database: ${MYSQL_HOST}")
        assert result == ["MYSQL_HOST"]

    def test_multiple_variables(self) -> None:
        """Test extracting multiple variables."""
        content = "host: ${MYSQL_HOST}\nuser: ${MYSQL_USER}\npass: ${MYSQL_PASSWORD}"
        result = extract_env_variables(content)
        assert result == ["MYSQL_HOST", "MYSQL_PASSWORD", "MYSQL_USER"]

    def test_duplicate_variables(self) -> None:
        """Test duplicate variables are deduplicated."""
        content = "primary: ${DB_HOST}\nsecondary: ${DB_HOST}"
        result = extract_env_variables(content)
        assert result == ["DB_HOST"]

    def test_no_variables(self) -> None:
        """Test no variables returns empty list."""
        content = "static: value\nother: constant"
        result = extract_env_variables(content)
        assert result == []

    def test_mixed_content(self) -> None:
        """Test mixed content with variables and static values."""
        content = "static: value\nenv: ${MY_VAR}\nother: constant\nenv2: ${OTHER_VAR}"
        result = extract_env_variables(content)
        assert result == ["MY_VAR", "OTHER_VAR"]

    def test_empty_string(self) -> None:
        """Test empty string returns empty list."""
        result = extract_env_variables("")
        assert result == []

    def test_invalid_syntax(self) -> None:
        """Test invalid syntax is not matched."""
        content = "invalid: ${INCOMPLETE"
        result = extract_env_variables(content)
        assert result == []

    def test_variable_with_underscores_and_numbers(self) -> None:
        """Test variable with underscores and numbers."""
        content = "var: ${MY_VAR_123}"
        result = extract_env_variables(content)
        assert result == ["MY_VAR_123"]


class TestParseEnvExample:
    """Test suite for parse_env_example function."""

    def test_basic_env_file(self) -> None:
        """Test parsing basic env file."""
        content = """# Database configuration
MYSQL_HOST=localhost
MYSQL_USER=admin
MYSQL_PASSWORD=secret123

# API keys
API_KEY=abc123
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".env", delete=False
        ) as tmpfile:
            tmpfile.write(content)
            tmpfile.flush()
            result = parse_env_example(Path(tmpfile.name))
            assert result == {
                "MYSQL_HOST": True,
                "MYSQL_USER": True,
                "MYSQL_PASSWORD": True,
                "API_KEY": True,
            }

    def test_env_file_with_comments_and_empty_lines(self) -> None:
        """Test parsing env file with comments and empty lines."""
        content = """# Comment line

VAR1=value1

# Another comment
VAR2=value2
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".env", delete=False
        ) as tmpfile:
            tmpfile.write(content)
            tmpfile.flush()
            result = parse_env_example(Path(tmpfile.name))
            assert result == {"VAR1": True, "VAR2": True}

    def test_env_file_with_special_characters(self) -> None:
        """Test parsing env file with special characters in values."""
        content = """DB_URL=postgresql://user:pass@localhost:5432/db
API_ENDPOINT=https://api.example.com/v1
SECRET_KEY=abc123!@#$%^&*()
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".env", delete=False
        ) as tmpfile:
            tmpfile.write(content)
            tmpfile.flush()
            result = parse_env_example(Path(tmpfile.name))
            assert result == {
                "DB_URL": True,
                "API_ENDPOINT": True,
                "SECRET_KEY": True,
            }

    def test_empty_file(self) -> None:
        """Test parsing empty file."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".env", delete=False
        ) as tmpfile:
            tmpfile.write("")
            tmpfile.flush()
            result = parse_env_example(Path(tmpfile.name))
            assert result == {}

    def test_only_comments(self) -> None:
        """Test parsing file with only comments."""
        content = """# Comment 1
# Comment 2
# Comment 3"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".env", delete=False
        ) as tmpfile:
            tmpfile.write(content)
            tmpfile.flush()
            result = parse_env_example(Path(tmpfile.name))
            assert result == {}

    def test_file_not_found(self) -> None:
        """Test parsing nonexistent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            parse_env_example(Path("/nonexistent/file.txt"))


class TestScanLocalConfigs:
    """Test suite for scan_local_configs function."""

    def test_single_file_with_variables(self) -> None:
        """Test scanning single file with variables."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.yaml"
            config_file.write_text(
                """
database:
  host: ${MYSQL_HOST}
  user: ${MYSQL_USER}
"""
            )
            result = scan_local_configs(Path(tmpdir))
            assert result == {"config.yaml": ["MYSQL_HOST", "MYSQL_USER"]}

    def test_multiple_files(self) -> None:
        """Test scanning multiple files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "db.yaml").write_text("host: ${DB_HOST}")
            (Path(tmpdir) / "api.yaml").write_text(
                """
key: ${API_KEY}
secret: ${API_SECRET}
"""
            )
            result = scan_local_configs(Path(tmpdir))
            assert result == {
                "db.yaml": ["DB_HOST"],
                "api.yaml": ["API_KEY", "API_SECRET"],
            }

    def test_files_without_variables(self) -> None:
        """Test scanning files without variables."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "static.yaml").write_text("value: constant")
            result = scan_local_configs(Path(tmpdir))
            assert result == {}

    def test_mixed_yaml_and_yml_extensions(self) -> None:
        """Test scanning files with both .yaml and .yml extensions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "config.yaml").write_text("var: ${VAR1}")
            (Path(tmpdir) / "settings.yml").write_text("var: ${VAR2}")
            result = scan_local_configs(Path(tmpdir))
            assert result == {
                "config.yaml": ["VAR1"],
                "settings.yml": ["VAR2"],
            }

    def test_ignore_non_yaml_files(self) -> None:
        """Test non-YAML files are ignored."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "config.yaml").write_text("var: ${MY_VAR}")
            (Path(tmpdir) / "readme.txt").write_text("var: ${IGNORED}")
            (Path(tmpdir) / "config.json").write_text('{"var": "${ALSO_IGNORED}"}')
            result = scan_local_configs(Path(tmpdir))
            assert result == {"config.yaml": ["MY_VAR"]}

    def test_nonexistent_directory(self) -> None:
        """Test scanning nonexistent directory raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            scan_local_configs(Path("/nonexistent/directory"))
