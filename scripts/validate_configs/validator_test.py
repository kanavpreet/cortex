"""Unit tests for config validation logic."""

import tempfile
from pathlib import Path

import pytest
import yaml

from .validator import (
    KUBE_ONLY_FILES,
    LOCAL_ONLY_DIRS,
    LOCAL_ONLY_FILES,
    ValidationResult,
    check_secrets_have_defaults,
    compare_yaml_structure,
    extract_kube_variables,
    extract_local_variables,
    load_yaml_safe,
    normalize_templates,
    validate_file_pair,
)


class TestExtractKubeVariables:
    """Test suite for extract_kube_variables function."""

    def test_single_env_variable(self) -> None:
        """Test extracting single Env variable."""
        assert extract_kube_variables("{{ .Env.MY_VAR }}") == ["MY_VAR"]

    def test_single_app_secrets_variable(self) -> None:
        """Test extracting single App.Secrets variable."""
        assert extract_kube_variables("{{ .App.Secrets.API_KEY }}") == ["API_KEY"]

    def test_nested_env_params_variable(self) -> None:
        """Test extracting nested Env.Params variable."""
        assert extract_kube_variables("{{ .Env.Params.port }}") == ["port"]

    def test_deeply_nested_variable(self) -> None:
        """Test extracting deeply nested variable."""
        assert extract_kube_variables("{{ .Env.Params.mysql.rdsEndpoint }}") == [
            "rdsEndpoint"
        ]

    def test_multiple_variables(self) -> None:
        """Test extracting multiple variables."""
        assert extract_kube_variables("{{ .Env.HOST }}:{{ .Env.PORT }}") == [
            "HOST",
            "PORT",
        ]

    def test_multiple_nested_variables(self) -> None:
        """Test extracting multiple nested variables."""
        assert extract_kube_variables(
            "{{ .Env.Params.host }}:{{ .Env.Params.port }}"
        ) == ["host", "port"]

    def test_no_variables(self) -> None:
        """Test no variables returns empty list."""
        assert extract_kube_variables("static value") == []

    def test_mixed_with_static_text(self) -> None:
        """Test extraction with mixed static text."""
        assert extract_kube_variables("url: https://{{ .Env.HOST }}/api") == ["HOST"]

    def test_with_extra_whitespace(self) -> None:
        """Test extraction with extra whitespace."""
        assert extract_kube_variables("{{  .Env.VAR  }}") == ["VAR"]

    def test_empty_string(self) -> None:
        """Test empty string returns empty list."""
        assert extract_kube_variables("") == []


class TestExtractLocalVariables:
    """Test suite for extract_local_variables function."""

    def test_single_variable(self) -> None:
        """Test extracting single variable."""
        assert extract_local_variables("${MY_VAR}") == ["MY_VAR"]

    def test_multiple_variables(self) -> None:
        """Test extracting multiple variables."""
        assert extract_local_variables("${HOST}:${PORT}") == ["HOST", "PORT"]

    def test_no_variables(self) -> None:
        """Test no variables returns empty list."""
        assert extract_local_variables("static value") == []

    def test_mixed_with_static_text(self) -> None:
        """Test extraction with mixed static text."""
        assert extract_local_variables("url: https://${HOST}/api") == ["HOST"]

    def test_empty_string(self) -> None:
        """Test empty string returns empty list."""
        assert extract_local_variables("") == []

    def test_incomplete_variable_syntax(self) -> None:
        """Test incomplete syntax is not matched."""
        assert extract_local_variables("${INCOMPLETE") == []


class TestNormalizeTemplates:
    """Test suite for normalize_templates function."""

    def test_kube_env_template(self) -> None:
        """Test normalizing kube Env template."""
        result = normalize_templates("value: {{ .Env.MY_VAR }}")
        assert result == "value: TEMPLATE_VALUE"

    def test_kube_app_secrets_template(self) -> None:
        """Test normalizing kube App.Secrets template."""
        result = normalize_templates("secret: {{ .App.Secrets.API_KEY }}")
        assert result == "secret: TEMPLATE_VALUE"

    def test_nested_env_params_template(self) -> None:
        """Test normalizing nested Env.Params template."""
        result = normalize_templates("port: {{ .Env.Params.port }}")
        assert result == "port: TEMPLATE_VALUE"

    def test_deeply_nested_template(self) -> None:
        """Test normalizing deeply nested template."""
        result = normalize_templates("endpoint: {{ .Env.Params.mysql.rdsEndpoint }}")
        assert result == "endpoint: TEMPLATE_VALUE"

    def test_local_envsubst_template(self) -> None:
        """Test normalizing local envsubst template."""
        result = normalize_templates("value: ${MY_VAR}")
        assert result == "value: TEMPLATE_MY_VAR"

    def test_mixed_templates(self) -> None:
        """Test normalizing mixed templates."""
        result = normalize_templates("kube: {{ .Env.VAR1 }}\nlocal: ${VAR2}")
        assert result == "kube: TEMPLATE_VALUE\nlocal: TEMPLATE_VAR2"

    def test_no_templates(self) -> None:
        """Test no templates returns unchanged."""
        result = normalize_templates("static: value")
        assert result == "static: value"

    def test_multiple_occurrences(self) -> None:
        """Test normalizing multiple occurrences."""
        result = normalize_templates("{{ .Env.HOST }}:{{ .Env.PORT }}")
        assert result == "TEMPLATE_VALUE:TEMPLATE_VALUE"

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


class TestLoadYamlSafe:
    """Test suite for load_yaml_safe function."""

    def test_valid_yaml_with_templates(self) -> None:
        """Test loading valid YAML with templates."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmpfile:
            tmpfile.write(
                """
database:
  host: {{ .Env.DB_HOST }}
  port: {{ .Env.DB_PORT }}
"""
            )
            tmpfile.flush()
            result = load_yaml_safe(Path(tmpfile.name))
            assert "database" in result

    def test_valid_yaml_with_envsubst(self) -> None:
        """Test loading valid YAML with envsubst."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmpfile:
            tmpfile.write(
                """
api:
  key: ${API_KEY}
  secret: ${API_SECRET}
"""
            )
            tmpfile.flush()
            result = load_yaml_safe(Path(tmpfile.name))
            assert "api" in result

    def test_empty_file(self) -> None:
        """Test loading empty file."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmpfile:
            tmpfile.write("")
            tmpfile.flush()
            result = load_yaml_safe(Path(tmpfile.name))
            assert result == {}

    def test_invalid_yaml(self) -> None:
        """Test loading invalid YAML raises exception."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmpfile:
            tmpfile.write(
                """
invalid:
  - item1
  misaligned: value
"""
            )
            tmpfile.flush()
            with pytest.raises(Exception):
                load_yaml_safe(Path(tmpfile.name))

    def test_file_not_found(self) -> None:
        """Test loading nonexistent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_yaml_safe(Path("/nonexistent/file.yaml"))


class TestCheckSecretsHaveDefaults:
    """Test suite for check_secrets_have_defaults function."""

    def test_secret_with_default(self) -> None:
        """Test secret with default passes."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmpfile:
            tmpfile.write('api_key: {{ .App.Secrets.api_key | default "" }}')
            tmpfile.flush()
            errors = check_secrets_have_defaults(Path(tmpfile.name))
            assert len(errors) == 0

    def test_secret_without_default(self) -> None:
        """Test secret without default fails."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmpfile:
            tmpfile.write("api_key: {{ .App.Secrets.api_key }}")
            tmpfile.flush()
            errors = check_secrets_have_defaults(Path(tmpfile.name))
            assert len(errors) == 1
            assert "Missing '| default' clause" in errors[0]

    def test_multiple_secrets_mixed(self) -> None:
        """Test multiple secrets with mixed defaults."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmpfile:
            tmpfile.write(
                """
key1: {{ .App.Secrets.key1 | default "" }}
key2: {{ .App.Secrets.key2 }}
"""
            )
            tmpfile.flush()
            errors = check_secrets_have_defaults(Path(tmpfile.name))
            assert len(errors) == 1
            assert ".App.Secrets.key2" in errors[0]


class TestCompareYamlStructure:
    """Test suite for compare_yaml_structure function."""

    def test_identical_simple_maps(self) -> None:
        """Test identical simple maps have no errors."""
        kube = {"key1": "value1", "key2": "value2"}
        local = {"key1": "value1", "key2": "value2"}
        errors = compare_yaml_structure(kube, local, "")
        assert len(errors) == 0

    def test_missing_key_in_local(self) -> None:
        """Test missing key in local detected."""
        kube = {"key1": "value1", "key2": "value2"}
        local = {"key1": "value1"}
        errors = compare_yaml_structure(kube, local, "")
        assert len(errors) == 1
        assert "Keys missing in local config" in errors[0]

    def test_extra_key_in_local(self) -> None:
        """Test extra key in local detected."""
        kube = {"key1": "value1"}
        local = {"key1": "value1", "key2": "value2"}
        errors = compare_yaml_structure(kube, local, "")
        assert len(errors) == 1
        assert "Extra keys in local config" in errors[0]

    def test_type_mismatch(self) -> None:
        """Test type mismatch detected."""
        kube = {"key": "string"}
        local = {"key": 123}
        errors = compare_yaml_structure(kube, local, "")
        assert len(errors) == 1
        assert "Type mismatch" in errors[0]

    def test_nested_structure_match(self) -> None:
        """Test nested structure matches."""
        kube = {"outer": {"inner": "value"}}
        local = {"outer": {"inner": "value"}}
        errors = compare_yaml_structure(kube, local, "")
        assert len(errors) == 0

    def test_list_match(self) -> None:
        """Test lists of same length pass."""
        kube = {"items": ["a", "b", "c"]}
        local = {"items": ["x", "y", "z"]}
        errors = compare_yaml_structure(kube, local, "")
        assert len(errors) == 0

    def test_list_length_mismatch(self) -> None:
        """Test list length mismatch detected."""
        kube = {"items": ["a", "b"]}
        local = {"items": ["x"]}
        errors = compare_yaml_structure(kube, local, "")
        assert len(errors) == 1
        assert "List length mismatch" in errors[0]

    def test_template_variable_in_local(self) -> None:
        """Test template variable in local passes type mismatch."""
        kube = {"port": 3306}
        local = {"port": "TEMPLATE_MYSQL_PORT"}
        errors = compare_yaml_structure(kube, local, "")
        assert len(errors) == 0

    def test_template_variable_in_kube(self) -> None:
        """Test template variable in kube passes type mismatch."""
        kube = {"value": "TEMPLATE_VALUE"}
        local = {"value": 123}
        errors = compare_yaml_structure(kube, local, "")
        assert len(errors) == 0


class TestValidateFilePair:
    """Test suite for validate_file_pair function."""

    def test_matching_files(self) -> None:
        """Test matching files pass validation."""
        kube_content = """
app:
  name: {{ .Env.APP_NAME }}
  port: {{ .Env.PORT }}
"""
        local_content = """
app:
  name: ${APP_NAME}
  port: ${PORT}
"""
        with (
            tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False
            ) as kube_file,
            tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False
            ) as local_file,
        ):
            kube_file.write(kube_content)
            kube_file.flush()
            local_file.write(local_content)
            local_file.flush()

            success, errors = validate_file_pair(
                Path(kube_file.name), Path(local_file.name)
            )
            assert success
            assert len(errors) == 0

    def test_missing_field_in_local(self) -> None:
        """Test missing field in local fails validation."""
        kube_content = """
app:
  name: {{ .Env.APP_NAME }}
  port: {{ .Env.PORT }}
"""
        local_content = """
app:
  name: ${APP_NAME}
"""
        with (
            tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False
            ) as kube_file,
            tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False
            ) as local_file,
        ):
            kube_file.write(kube_content)
            kube_file.flush()
            local_file.write(local_content)
            local_file.flush()

            success, errors = validate_file_pair(
                Path(kube_file.name), Path(local_file.name)
            )
            assert not success
            assert len(errors) > 0

    def test_both_empty(self) -> None:
        """Test both files empty fails validation."""
        with (
            tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False
            ) as kube_file,
            tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False
            ) as local_file,
        ):
            success, errors = validate_file_pair(
                Path(kube_file.name), Path(local_file.name)
            )
            assert not success
            assert "Both files are empty" in errors[0]

    def test_nonexistent_kube_file(self) -> None:
        """Test nonexistent kube file fails."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as local_file:
            success, errors = validate_file_pair(
                Path("/nonexistent/file.yaml"), Path(local_file.name)
            )
            assert not success
            assert len(errors) > 0


class TestReverseFileCheck:
    """Test that local configs missing from kube/files are detected."""

    def test_local_file_missing_in_kube(self) -> None:
        """Test that a local config with no kube counterpart is detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            kube_dir = Path(tmpdir) / "kube"
            local_dir = Path(tmpdir) / "local"
            kube_dir.mkdir()
            local_dir.mkdir()

            # kube has file-a, local has file-a and file-b
            (kube_dir / "file-a.yaml").write_text("key: value\n")
            (local_dir / "file-a.yaml").write_text("key: value\n")
            (local_dir / "file-b.yaml").write_text("key: value\n")

            kube_filenames = {f.name for f in kube_dir.rglob("*.yaml")}
            local_files = list(local_dir.rglob("*.yaml"))

            missing = [f for f in local_files if f.name not in kube_filenames]
            assert len(missing) == 1
            assert missing[0].name == "file-b.yaml"

    def test_no_missing_files_in_either_direction(self) -> None:
        """Test that symmetric directories produce no missing files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            kube_dir = Path(tmpdir) / "kube"
            local_dir = Path(tmpdir) / "local"
            kube_dir.mkdir()
            local_dir.mkdir()

            (kube_dir / "config.yaml").write_text("key: value\n")
            (local_dir / "config.yaml").write_text("key: value\n")

            kube_filenames = {f.name for f in kube_dir.rglob("*.yaml")}
            local_files = list(local_dir.rglob("*.yaml"))

            missing = [f for f in local_files if f.name not in kube_filenames]
            assert len(missing) == 0

    def test_local_only_files_are_exempt(self) -> None:
        """Test that LOCAL_ONLY_FILES are skipped in the reverse check."""
        with tempfile.TemporaryDirectory() as tmpdir:
            kube_dir = Path(tmpdir) / "kube"
            local_dir = Path(tmpdir) / "local"
            kube_dir.mkdir()
            local_dir.mkdir()

            exempt_file = next(iter(LOCAL_ONLY_FILES))
            (local_dir / exempt_file).write_text("key: value\n")

            kube_filenames = {f.name for f in kube_dir.rglob("*.yaml")}
            local_files = list(local_dir.rglob("*.yaml")) + list(
                local_dir.rglob("*.yml")
            )

            missing = [
                f
                for f in local_files
                if f.name not in kube_filenames
                and f.name not in LOCAL_ONLY_FILES
                and not any(part in LOCAL_ONLY_DIRS for part in f.parts)
            ]
            assert len(missing) == 0

    def test_local_only_dirs_are_exempt(self) -> None:
        """Test that files inside LOCAL_ONLY_DIRS are skipped in the reverse check."""
        with tempfile.TemporaryDirectory() as tmpdir:
            kube_dir = Path(tmpdir) / "kube"
            local_dir = Path(tmpdir) / "local"
            kube_dir.mkdir()
            local_dir.mkdir()

            exempt_dir = next(iter(LOCAL_ONLY_DIRS))
            nested = local_dir / exempt_dir / "provisioning"
            nested.mkdir(parents=True)
            (nested / "datasources.yml").write_text("key: value\n")

            kube_filenames = {f.name for f in kube_dir.rglob("*.yaml")}
            local_files = list(local_dir.rglob("*.yaml")) + list(
                local_dir.rglob("*.yml")
            )

            missing = [
                f
                for f in local_files
                if f.name not in kube_filenames
                and f.name not in LOCAL_ONLY_FILES
                and not any(part in LOCAL_ONLY_DIRS for part in f.parts)
            ]
            assert len(missing) == 0

    def test_kube_only_files_are_exempt(self) -> None:
        """Test that KUBE_ONLY_FILES are skipped in the forward check."""
        with tempfile.TemporaryDirectory() as tmpdir:
            kube_dir = Path(tmpdir) / "kube"
            local_dir = Path(tmpdir) / "local"
            kube_dir.mkdir()
            local_dir.mkdir()

            exempt_file = next(iter(KUBE_ONLY_FILES))
            (kube_dir / exempt_file).write_text("key: value\n")

            kube_files = list(kube_dir.rglob("*.yaml")) + list(kube_dir.rglob("*.yml"))

            missing = [
                f
                for f in kube_files
                if f.name not in KUBE_ONLY_FILES and not (local_dir / f.name).exists()
            ]
            assert len(missing) == 0


class TestValidationResult:
    """Test suite for ValidationResult dataclass."""

    def test_successful_validation(self) -> None:
        """Test successful validation result."""
        result = ValidationResult(filename="test.yaml", success=True, errors=[])
        assert result.filename == "test.yaml"
        assert result.success is True
        assert len(result.errors) == 0

    def test_failed_validation(self) -> None:
        """Test failed validation result."""
        result = ValidationResult(
            filename="error.yaml", success=False, errors=["error1", "error2"]
        )
        assert result.filename == "error.yaml"
        assert result.success is False
        assert len(result.errors) == 2
