# Pre-commit Hooks Setup

This repository uses **pre-commit hooks** to maintain code quality and keep documentation synchronized with code changes. Pre-commit runs automated checks before each commit to ensure code meets quality standards.

## What Are Pre-commit Hooks?

Pre-commit hooks are automated scripts that run before each Git commit. They help catch issues early by:
- Formatting code automatically (ruff format)
- Running tests (pytest)
- Performing static analysis and linting (ruff check, mypy)
- Ensuring code coverage meets thresholds
- Validating configuration files consistency

## Configured Hooks

### Code Quality Hooks (Python-specific)
- **format-python**: Formats Python code with Ruff (Black-compatible)
- **lint-python**: Lints Python code with Ruff (replaces flake8, isort, pyupgrade, etc.)
- **unit-tests**: Runs all unit tests with pytest and enforces coverage requirements

### Test Configuration
- **Test discovery**: Automatically finds `*_test.py` files alongside source code
- **Coverage enforcement**: Requires 95% test coverage
- **Coverage exclusions**: Test files (`*_test.py`) are excluded from coverage metrics

## Prerequisites

### 1. Install uv (Python Package Manager)

```bash
# macOS
brew install uv

# Or via curl
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Install Pre-commit

```bash
# macOS
brew install pre-commit

# Or via uv
uv tool install pre-commit
```

### 3. Install Pre-commit Hooks

```bash
# From repository root
pre-commit install
```

This will install all configured hooks into your local `.git/hooks/` directory.

### 4. Set Up Python Environment

```bash
# Navigate to matik Python directory
cd matik

# Install dependencies with uv
uv sync

# Verify installation
uv run pytest --version
uv run ruff --version
uv run mypy --version
```

## Usage

### Normal Workflow

1. Make your code changes
2. Stage your changes:
   ```bash
   git add matik/api/__main__.py
   ```

3. Attempt to commit:
   ```bash
   git commit -m "Add API endpoint"
   ```

4. Pre-commit hooks will automatically run:
   - **Code formatting**: `ruff format` will format your code (Black-compatible, 88 chars)
   - **Linting**: `ruff check` will check code quality (imports, naming, bugs, complexity, etc.)
   - **Tests**: `pytest` will run all unit tests with coverage requirements (95%)

5. If all hooks pass, the commit succeeds:
   ```
   Format Python code with Ruff.....................................Passed
   Lint Python code with Ruff.......................................Passed
   Run Python unit tests............................................Passed
   ✅ All pre-commit hooks passed!
   [branch-name abc1234] Add API endpoint
   ```

6. If any hook fails, the commit is blocked:
   - Review the error messages
   - Fix the issues (formatting is auto-fixed, just stage the changes)
   - Re-run the commit

## Configuration

### Pre-commit Configuration

The pre-commit configuration is in:
```
matik/.pre-commit-config.yaml
```

Example hook configuration:
```yaml
- repo: local
  hooks:
    - id: format-python
      name: Format Python code with Ruff
      entry: uv run --python 3.13 ruff format .
      language: system
      pass_filenames: false
      types: [python]
      verbose: true
      stages: [pre-commit]

    - id: lint-python
      name: Lint Python code with Ruff
      entry: uv run --python 3.13 ruff check .
      language: system
      pass_filenames: false
      types: [python]
      verbose: true
      stages: [pre-commit]

    - id: unit-tests
      name: Run Python unit tests
      entry: uv run --directory matik --python 3.13 pytest
      language: system
      pass_filenames: false
      types: [python]
      verbose: true
      stages: [pre-commit]
```

### Python Configuration Files

Additional Python tool configurations:

- **pyproject.toml**: Project metadata, dependencies, and tool configuration
- **pytest.toml**: Pytest settings including test discovery pattern (`*_test.py`)
- **ruff.toml**: Ruff linting and formatting rules
- **mypy.ini**: Strict type checking configuration

You can customize:
- Which hooks to enable/disable
- Ruff linting rules in `ruff.toml`
- Coverage thresholds in `pytest.toml` (currently 95%)
- Type checking strictness in `mypy.ini`

## Skipping Hooks

### Skip All Hooks

To bypass all pre-commit hooks (use sparingly):

```bash
git commit --no-verify -m "message"
# or
git commit -n -m "message"
```

### Skip Specific Hooks

To skip only certain hooks:

```bash
# Skip only the unit tests hook
SKIP=unit-tests git commit -m "WIP: work in progress"

# Skip multiple hooks
SKIP=lint-python,unit-tests git commit -m "WIP: refactoring"

# Skip only formatting
SKIP=format-python git commit -m "message"
```

**Note**: Skipping hooks should be done carefully and only when necessary (e.g., work-in-progress commits, urgent fixes).

## Troubleshooting

### Pre-commit hooks not running

If hooks aren't running at all:

```bash
# Check if pre-commit is installed
pre-commit --version

# Check if uv is installed
uv --version

# Reinstall hooks
pre-commit install

# Verify hooks are installed
ls -la .git/hooks/pre-commit
```

### Hook failures

If a specific hook is failing:

```bash
# Run a specific hook manually
pre-commit run <hook-id>

# Example: run unit tests
pre-commit run unit-tests

# Example: run linting
pre-commit run lint-python

# Run all hooks on all files (not just staged)
pre-commit run --all-files
```

### Code formatting issues

If `ruff format` makes changes:
1. The changes are automatically applied to your files
2. Stage the formatted files: `git add <files>`
3. Re-run the commit

Example:
```bash
git add matik/api/__main__.py
git commit -m "Update API"
# Ruff formats the file
git add matik/api/__main__.py  # Stage formatted version
git commit -m "Update API"     # Commit again
```

### Linting errors

If `ruff check` fails:
1. Review the error messages (file:line - error code and description)
2. Fix the issues manually or run `ruff check --fix` for auto-fixable issues
3. Stage the fixes and re-commit

Common linting errors:
- **F401**: Unused imports (remove them)
- **E501**: Line too long (break into multiple lines or use Black-compatible formatting)
- **N802**: Function name should be lowercase (rename using snake_case)
- **UP**: Outdated Python syntax (use modern syntax)

### Test failures

If `pytest` fails:
1. Review the test output to identify failing tests
2. Fix the failing tests or the code being tested
3. Run tests locally to verify: `cd matik && uv run pytest -v`
4. Stage the fixes and re-commit

### Coverage failures

If coverage is below 95%:
1. Check the coverage report: `cd matik && uv run pytest`
2. Add tests for uncovered code
3. Verify coverage: `uv run pytest --cov-report=term-missing`
4. Stage new tests and re-commit

### Type checking errors (mypy)

If you run mypy manually and it fails:
1. Review the type errors
2. Add type annotations: `def func() -> None:` instead of `def func():`
3. Use proper types: `list[str]` instead of `list`
4. Import types: `from typing import Optional`

### uv or Python version issues

If uv commands fail:
```bash
# Check Python version
python --version  # Should be 3.13+

# Reinstall uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Sync dependencies
cd matik && uv sync

# Clear cache if needed
uv cache clean
```

### Hook always skips

Check if you're modifying Python files:
```bash
git diff --cached --name-only
```

The hooks only run when staged files match the `types: [python]` pattern in `.pre-commit-config.yaml`.

## Best Practices

1. **Run tests locally**: Ensure tests pass before committing
   ```bash
   cd matik && uv run pytest -v
   ```

2. **Check formatting before commit**: Preview what ruff will change
   ```bash
   cd matik && uv run ruff format --check .
   ```

3. **Fix linting issues**: Address linting errors proactively
   ```bash
   cd matik && uv run ruff check --fix .
   ```

4. **Verify type annotations**: Run mypy to catch type errors
   ```bash
   cd matik && uv run mypy .
   ```

5. **Write tests alongside code**: Follow the `*_test.py` pattern
   - Place test files next to source files
   - Example: `api/__main__.py` → `api/main_test.py`

6. **Maintain coverage**: Keep test coverage above 95%
   - Check coverage: `cd matik && uv run pytest --cov-report=term-missing`
   - Add tests for new code before committing

7. **Review formatting changes**: Check auto-formatted code before re-staging

8. **Fix issues promptly**: Address hook failures immediately rather than bypassing with `--no-verify`

## Disabling Hooks

To disable specific hooks:

1. **Temporarily**: Use `SKIP=<hook-id>` before commits (see "Skipping Hooks" section)
   ```bash
   SKIP=unit-tests git commit -m "WIP"
   ```

2. **Permanently**: Comment out the hook in `matik/.pre-commit-config.yaml`:
   ```yaml
   # - id: unit-tests
   #   name: Run Python unit tests
   #   entry: uv run --directory matik --python 3.13 pytest
   #   ...
   ```

3. Re-install hooks:
   ```bash
   pre-commit install
   ```

## Examples

### Example 1: Adding a New Service Module

```bash
# 1. Create new module
cat > matik/api/routes.py << 'EOF'
"""API route handlers."""

def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy"}
EOF

# 2. Create test file beside it
cat > matik/api/routes_test.py << 'EOF'
"""Tests for API route handlers."""

from api.routes import health_check


def test_health_check_returns_healthy_status() -> None:
    """Test that health check returns expected status."""
    result = health_check()
    assert result["status"] == "healthy"
EOF

# 3. Run tests locally
cd matik && uv run pytest -v

# 4. Stage changes
git add matik/api/routes.py matik/api/routes_test.py

# 5. Commit (pre-commit hooks will run automatically)
git commit -m "Add health check endpoint"

# 6. If formatting changes were made, stage and re-commit
git add matik/api/routes.py matik/api/routes_test.py
git commit -m "Add health check endpoint"
```

### Example 2: Fixing Linting Errors

```bash
# 1. Make code changes
vim matik/historian/__main__.py

# 2. Check for linting issues
cd matik && uv run ruff check .

# 3. Auto-fix issues
uv run ruff check --fix .

# 4. Review changes
git diff

# 5. Stage and commit
git add matik/historian/__main__.py
git commit -m "Fix linting issues in historian"
```

### Example 3: Adding Tests to Improve Coverage

```bash
# 1. Check current coverage
cd matik && uv run pytest --cov-report=term-missing

# 2. Identify uncovered lines (shown in "Missing" column)

# 3. Add tests for uncovered code
vim matik/api/main_test.py

# 4. Verify coverage improved
uv run pytest --cov-report=term-missing

# 5. Stage and commit
git add matik/api/main_test.py
git commit -m "Add tests to improve coverage"
```

## Contributing

When adding new hooks or modifying pre-commit configuration:
1. Update the hook configuration in `matik/.pre-commit-config.yaml`
2. Test the hook manually: `pre-commit run <hook-id> --all-files`
3. Update this documentation to reflect the changes
4. Ensure the hook works with `uv` and Python 3.13

## Additional Resources

- [Pre-commit Framework](https://pre-commit.com/)
- [uv Documentation](https://docs.astral.sh/uv/)
- [Ruff Documentation](https://docs.astral.sh/ruff/)
- [Pytest Documentation](https://docs.pytest.org/)
- [Python Type Hints Guide](https://docs.python.org/3/library/typing.html)
- [pytest-cov Coverage Plugin](https://pytest-cov.readthedocs.io/)
