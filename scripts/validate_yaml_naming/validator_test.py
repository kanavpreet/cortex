"""Unit tests for YAML naming convention validation logic."""

import tempfile
from pathlib import Path

import yaml

from .validator import (
    EXCLUDE_PATTERNS,
    FileValidationResult,
    ValidationError,
    is_snake_case,
    is_template_value,
    normalize_templates,
    should_exclude_file,
    validate_yaml_file,
    validate_yaml_keys,
)


class TestIsSnakeCase:
    """Test suite for is_snake_case function."""

    def test_valid_single_word(self) -> None:
        """Test valid single word."""
        assert is_snake_case("test")

    def test_valid_snake_case(self) -> None:
        """Test valid snake_case."""
        assert is_snake_case("test_case")

    def test_valid_with_numbers(self) -> None:
        """Test valid with numbers."""
        assert is_snake_case("test123")

    def test_valid_snake_case_with_numbers(self) -> None:
        """Test valid snake_case with numbers."""
        assert is_snake_case("test_case_123")

    def test_valid_multiple_underscores(self) -> None:
        """Test valid multiple segments."""
        assert is_snake_case("this_is_a_test")

    def test_valid_with_numbers_in_middle(self) -> None:
        """Test valid with numbers in middle."""
        assert is_snake_case("test_123_case")

    def test_invalid_starts_with_uppercase(self) -> None:
        """Test invalid when starts with uppercase."""
        assert not is_snake_case("Test")

    def test_invalid_contains_uppercase(self) -> None:
        """Test invalid when contains uppercase."""
        assert not is_snake_case("testCase")

    def test_invalid_starts_with_number(self) -> None:
        """Test invalid when starts with number."""
        assert not is_snake_case("123test")

    def test_invalid_starts_with_underscore(self) -> None:
        """Test invalid when starts with underscore."""
        assert not is_snake_case("_test")

    def test_invalid_ends_with_underscore(self) -> None:
        """Test invalid when ends with underscore."""
        assert not is_snake_case("test_")

    def test_invalid_consecutive_underscores(self) -> None:
        """Test invalid when consecutive underscores."""
        assert not is_snake_case("test__case")

    def test_invalid_contains_hyphen(self) -> None:
        """Test invalid when contains hyphen."""
        assert not is_snake_case("test-case")

    def test_invalid_contains_space(self) -> None:
        """Test invalid when contains space."""
        assert not is_snake_case("test case")

    def test_invalid_contains_special_chars(self) -> None:
        """Test invalid when contains special characters."""
        assert not is_snake_case("test@case")

    def test_invalid_empty_string(self) -> None:
        """Test invalid empty string."""
        assert not is_snake_case("")

    def test_invalid_only_underscore(self) -> None:
        """Test invalid underscore only."""
        assert not is_snake_case("_")

    def test_invalid_camel_case(self) -> None:
        """Test invalid camelCase."""
        assert not is_snake_case("camelCase")

    def test_invalid_pascal_case(self) -> None:
        """Test invalid PascalCase."""
        assert not is_snake_case("PascalCase")

    def test_invalid_upper_snake_case(self) -> None:
        """Test invalid UPPER_SNAKE_CASE."""
        assert not is_snake_case("UPPER_CASE")


class TestIsTemplateValue:
    """Test suite for is_template_value function."""

    def test_k8s_template_simple(self) -> None:
        """Test K8s template is detected."""
        assert is_template_value("{{ .Env.VAR }}")

    def test_k8s_template_with_params(self) -> None:
        """Test K8s template with params is detected."""
        assert is_template_value("{{ .Env.Params.VAR }}")

    def test_k8s_template_with_secrets(self) -> None:
        """Test K8s template with secrets is detected."""
        assert is_template_value("{{ .App.Secrets.VAR }}")

    def test_k8s_template_in_string(self) -> None:
        """Test K8s template in string is detected."""
        assert is_template_value("prefix {{ .Env.VAR }} suffix")

    def test_envsubst_simple(self) -> None:
        """Test envsubst template is detected."""
        assert is_template_value("${VAR}")

    def test_envsubst_in_string(self) -> None:
        """Test envsubst template in string is detected."""
        assert is_template_value("prefix ${VAR} suffix")

    def test_plain_string(self) -> None:
        """Test plain string is not detected as template."""
        assert not is_template_value("just a string")

    def test_empty_string(self) -> None:
        """Test empty string is not detected as template."""
        assert not is_template_value("")

    def test_number(self) -> None:
        """Test number is not detected as template."""
        assert not is_template_value(123)

    def test_boolean(self) -> None:
        """Test boolean is not detected as template."""
        assert not is_template_value(True)

    def test_none(self) -> None:
        """Test None is not detected as template."""
        assert not is_template_value(None)

    def test_dict(self) -> None:
        """Test dict is not detected as template."""
        assert not is_template_value({"key": "value"})

    def test_incomplete_k8s_template(self) -> None:
        """Test incomplete K8s template is not detected."""
        assert not is_template_value("{{ .Env.VAR")

    def test_incomplete_envsubst(self) -> None:
        """Test incomplete envsubst is not detected."""
        assert not is_template_value("${VAR")


class TestNormalizeTemplates:
    """Test suite for normalize_templates function."""

    def test_k8s_env_template(self) -> None:
        """Test normalizing K8s Env template."""
        result = normalize_templates("value: {{ .Env.VAR }}")
        assert result == "value: TEMPLATE_VALUE"

    def test_k8s_env_params_template(self) -> None:
        """Test normalizing K8s Env.Params template."""
        result = normalize_templates("value: {{ .Env.Params.VAR }}")
        assert result == "value: TEMPLATE_VALUE"

    def test_k8s_app_secrets_template(self) -> None:
        """Test normalizing K8s App.Secrets template."""
        result = normalize_templates("value: {{ .App.Secrets.KEY }}")
        assert result == "value: TEMPLATE_VALUE"

    def test_envsubst_template(self) -> None:
        """Test normalizing envsubst template."""
        result = normalize_templates("value: ${VAR}")
        assert result == "value: TEMPLATE_VALUE"

    def test_multiple_templates(self) -> None:
        """Test normalizing multiple templates."""
        result = normalize_templates("a: {{ .Env.A }}\nb: ${B}")
        assert result == "a: TEMPLATE_VALUE\nb: TEMPLATE_VALUE"

    def test_no_templates(self) -> None:
        """Test no templates returns unchanged."""
        result = normalize_templates("plain: value")
        assert result == "plain: value"

    def test_go_template_if_line(self) -> None:
        """Test a standalone `{{ if }}` line is blanked, not left dangling."""
        result = normalize_templates('{{ if eq .Env.Name "sandbox" }}')
        assert result == ""

    def test_go_template_else_line(self) -> None:
        """Test a standalone `{{ else }}` line is blanked."""
        result = normalize_templates("{{ else }}")
        assert result == ""

    def test_go_template_end_line(self) -> None:
        """Test a standalone `{{ end }}` line is blanked."""
        result = normalize_templates("{{ end }}")
        assert result == ""

    def test_go_template_conditional_block_parses_as_valid_yaml(self) -> None:
        """Test a full if/else/end block around key: value lines normalizes to
        parseable YAML -- this is the real repro case (a bare control-flow
        line broke yaml.safe_load before control-flow lines were blanked)."""
        content = (
            "mysql:\n"
            '{{ if eq .Env.Name "sandbox" }}\n'
            "  database: matik_staging\n"
            "{{ else }}\n"
            "  database: {{ .Env.Params.mysql.rds_database }}\n"
            "{{ end }}\n"
        )
        normalized = normalize_templates(content)
        parsed = yaml.safe_load(normalized)
        assert parsed == {"mysql": {"database": "TEMPLATE_VALUE"}}


class TestValidateYamlKeys:
    """Test suite for validate_yaml_keys function."""

    def test_valid_simple_map(self) -> None:
        """Test valid simple map has no errors."""
        data = {"valid_key": "value", "another": "value"}
        errors = validate_yaml_keys(data, "")
        assert len(errors) == 0

    def test_valid_nested_map(self) -> None:
        """Test valid nested map has no errors."""
        data = {"parent": {"child_key": "value"}}
        errors = validate_yaml_keys(data, "")
        assert len(errors) == 0

    def test_invalid_camel_case_key(self) -> None:
        """Test camelCase key is detected."""
        data = {"camelCase": "value"}
        errors = validate_yaml_keys(data, "")
        assert len(errors) == 2  # fails snake_case and invalid chars
        assert any("does not follow snake_case" in e.message for e in errors)
        assert errors[0].key == "camelCase"

    def test_invalid_hyphenated_key(self) -> None:
        """Test hyphenated key is detected."""
        data = {"kebab-case": "value"}
        errors = validate_yaml_keys(data, "")
        assert len(errors) == 3  # fails snake_case, contains hyphen, invalid chars
        assert errors[0].key == "kebab-case"

    def test_key_with_spaces(self) -> None:
        """Test key with spaces is detected."""
        data = {"has spaces": "value"}
        errors = validate_yaml_keys(data, "")
        assert any(e.message == "Key 'has spaces' contains spaces" for e in errors)

    def test_key_with_consecutive_underscores(self) -> None:
        """Test key with consecutive underscores is detected."""
        data = {"double__underscore": "value"}
        errors = validate_yaml_keys(data, "")
        assert any("contains consecutive underscores" in e.message for e in errors)

    def test_key_starts_with_underscore(self) -> None:
        """Test key starting with underscore is detected."""
        data = {"_leading": "value"}
        errors = validate_yaml_keys(data, "")
        assert any("starts or ends with underscore" in e.message for e in errors)

    def test_key_ends_with_underscore(self) -> None:
        """Test key ending with underscore is detected."""
        data = {"trailing_": "value"}
        errors = validate_yaml_keys(data, "")
        assert any("starts or ends with underscore" in e.message for e in errors)

    def test_nested_path_validation(self) -> None:
        """Test nested path is included in error message."""
        data = {"parent": {"badKey": "value"}}
        errors = validate_yaml_keys(data, "")
        assert any("parent.badKey" in e.path for e in errors)

    def test_array_with_nested_maps(self) -> None:
        """Test array with nested maps is validated."""
        data = [{"valid_key": "value"}, {"BadKey": "value"}]
        errors = validate_yaml_keys(data, "items")
        assert any("[1].BadKey" in e.path for e in errors)

    def test_skip_template_values(self) -> None:
        """Test template values are skipped."""
        data = {"badKey": "{{ .Env.VAR }}"}
        errors = validate_yaml_keys(data, "")
        assert len(errors) == 0  # template values are skipped

    def test_empty_map(self) -> None:
        """Test empty map has no errors."""
        data: dict[str, str] = {}
        errors = validate_yaml_keys(data, "")
        assert len(errors) == 0


class TestValidateYamlFile:
    """Test suite for validate_yaml_file function."""

    def test_valid_yaml_file(self) -> None:
        """Test valid YAML file passes."""
        content = """
valid_key: value
another_key:
  nested_key: value
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmpfile:
            tmpfile.write(content)
            tmpfile.flush()
            success, errors = validate_yaml_file(Path(tmpfile.name))
            assert success
            assert len(errors) == 0

    def test_invalid_camel_case_key(self) -> None:
        """Test camelCase key fails validation."""
        content = """
validKey: value
invalidKey: value
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmpfile:
            tmpfile.write(content)
            tmpfile.flush()
            success, errors = validate_yaml_file(Path(tmpfile.name))
            assert not success
            assert any("does not follow snake_case" in e.message for e in errors)

    def test_yaml_with_templates(self) -> None:
        """Test YAML with templates passes."""
        content = """
valid_key: {{ .Env.VAR }}
another_key: ${TOKEN}
nested:
  template_value: {{ .App.Secrets.KEY }}
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmpfile:
            tmpfile.write(content)
            tmpfile.flush()
            success, errors = validate_yaml_file(Path(tmpfile.name))
            assert success
            assert len(errors) == 0

    def test_empty_yaml(self) -> None:
        """Test empty YAML passes."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmpfile:
            tmpfile.write("")
            tmpfile.flush()
            success, errors = validate_yaml_file(Path(tmpfile.name))
            assert success
            assert len(errors) == 0

    def test_invalid_yaml_syntax(self) -> None:
        """Test invalid YAML syntax fails."""
        content = "invalid: [yaml"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmpfile:
            tmpfile.write(content)
            tmpfile.flush()
            success, errors = validate_yaml_file(Path(tmpfile.name))
            assert not success
            assert any("Failed to parse YAML" in e.message for e in errors)

    def test_nonexistent_file(self) -> None:
        """Test nonexistent file fails."""
        success, errors = validate_yaml_file(Path("/non/existent/file.yaml"))
        assert not success
        assert len(errors) == 1
        assert "Failed to read file" in errors[0].message


class TestValidationError:
    """Test suite for ValidationError dataclass."""

    def test_validation_error_attributes(self) -> None:
        """Test ValidationError attributes."""
        error = ValidationError(
            path="config.database.host",
            key="host",
            message="Test error message",
        )
        assert error.path == "config.database.host"
        assert error.key == "host"
        assert error.message == "Test error message"


class TestFileValidationResult:
    """Test suite for FileValidationResult dataclass."""

    def test_successful_result(self) -> None:
        """Test successful result attributes."""
        result = FileValidationResult(
            filename="test.yaml",
            success=True,
            errors=[],
        )
        assert result.filename == "test.yaml"
        assert result.success is True
        assert len(result.errors) == 0

    def test_failed_result(self) -> None:
        """Test failed result attributes."""
        result = FileValidationResult(
            filename="test.yaml",
            success=False,
            errors=[ValidationError(path="key", key="key", message="error")],
        )
        assert not result.success
        assert len(result.errors) == 1


class TestExcludePatterns:
    """Test suite for EXCLUDE_PATTERNS constant."""

    def test_exclude_patterns_is_list(self) -> None:
        """Test EXCLUDE_PATTERNS is a list."""
        assert isinstance(EXCLUDE_PATTERNS, list)

    def test_exclude_patterns_contains_grafana_provisioning(self) -> None:
        """Test EXCLUDE_PATTERNS contains grafana provisioning directory."""
        assert any("grafana/provisioning/" in pattern for pattern in EXCLUDE_PATTERNS)


class TestShouldExcludeFile:
    """Test suite for should_exclude_file function."""

    def test_excludes_matching_file(self) -> None:
        """Test file matching exclude pattern is excluded."""
        repo_root = Path("/repo")
        filepath = Path(
            "/repo/matik/local-configs/grafana/provisioning/dashboards/dashboards.yml"
        )
        assert should_exclude_file(filepath, repo_root)

    def test_does_not_exclude_non_matching_file(self) -> None:
        """Test file not matching exclude pattern is not excluded."""
        repo_root = Path("/repo")
        filepath = Path("/repo/matik/local-configs/metrics.yml")
        assert not should_exclude_file(filepath, repo_root)

    def test_does_not_exclude_similar_filename(self) -> None:
        """Test similar filename is not excluded."""
        repo_root = Path("/repo")
        filepath = Path("/repo/matik/local-configs/other/dashboards.yml")
        assert not should_exclude_file(filepath, repo_root)

    def test_excludes_with_different_repo_root(self) -> None:
        """Test exclusion works with different repo roots."""
        repo_root = Path("/home/user/projects/matik")
        filepath = Path(
            "/home/user/projects/matik/matik/local-configs/grafana/"
            "provisioning/dashboards/dashboards.yml"
        )
        assert should_exclude_file(filepath, repo_root)

    def test_handles_filepath_outside_repo(self) -> None:
        """Test handles filepath outside repo gracefully."""
        repo_root = Path("/repo")
        filepath = Path("/other/path/file.yml")
        # Should not crash and should not exclude (no match)
        assert not should_exclude_file(filepath, repo_root)

    def test_does_not_exclude_yaml_files_by_default(self) -> None:
        """Test regular YAML files are not excluded."""
        repo_root = Path("/repo")
        test_files = [
            Path("/repo/_infra/kube/files/config.yml"),
            Path("/repo/matik/local-configs/metrics.yaml"),
            Path("/repo/matik/local-configs/facade-config.yml"),
        ]
        for filepath in test_files:
            assert not should_exclude_file(filepath, repo_root)
