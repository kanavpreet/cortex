# Database Migrations

Migrations are auto-generated from SQLModel models using Alembic, the only time when we would create migrations manually is if we need to make data changes (INSERT, UPDATE, DELETE).

## CLI Commands

The migrator supports the following commands:

```bash
# Upgrade to latest (default behavior)
python -m migrator
python -m migrator upgrade
python -m migrator upgrade <revision>   # Upgrade to specific revision

# Downgrade
python -m migrator downgrade -1         # Downgrade by 1 revision
python -m migrator downgrade -2         # Downgrade by 2 revisions
python -m migrator downgrade base       # Downgrade to empty database
python -m migrator downgrade <revision> # Downgrade to specific revision

# Inspect state
python -m migrator current              # Show current revision
python -m migrator history              # Show migration history

# Stamp (mark revision without running migrations)
python -m migrator stamp head           # Mark as up-to-date
python -m migrator stamp <revision>     # Mark at specific revision
```

### Docker Compose Usage

```bash
# Run any command via docker-compose
docker-compose run --rm \
  -v "$(pwd)/migrator/alembic/versions:/app/migrator/alembic/versions" \
  migrator python -m migrator <command> [args]

# Examples:
docker-compose run migrator python -m migrator current
docker-compose run migrator python -m migrator downgrade -1
docker-compose run migrator python -m migrator stamp head
```

## Creating a Migration Automatically

### 1. Add or Modify Model

Create or update your model in `common/models/` (ensure `table=True` for DB tables).

### 2. Import Model in `__init__.py`

Import your new model in `common/models/__init__.py` and add it to `__all__`:
```python
from common.models.your_model import YourModel

__all__ = [
    ...
    "YourModel",
]
```

### 3. Start Services

```bash
docker-compose up -d mysql adminer
docker-compose build migrator
docker-compose run --rm \
  -v "$(pwd)/migrator/alembic/versions:/app/migrator/alembic/versions" \
  migrator python -m migrator
```

### 4. Create migration

Generate migration:
```bash
docker-compose run --rm \
  -v "$(pwd)/migrator/alembic/versions:/app/migrator/alembic/versions" \
  migrator python -m alembic -c /app/migrator/alembic.ini revision --autogenerate -m "description"
```

### 5. Apply migration

Review the generated file in `migrator/alembic/versions/`, then apply:
```bash
docker-compose run --rm \
  -v "$(pwd)/migrator/alembic/versions:/app/migrator/alembic/versions" \
  migrator python -m migrator
```

## Manual Migrations (Data Changes)

For data migrations (INSERT, UPDATE, DELETE), create an empty migration without `--autogenerate`:

```bash
cd matik
uv run alembic -c migrator/alembic.ini revision -m "insert_initial_data"
```

Then use `op.execute()` for raw SQL:

```python
def upgrade() -> None:
    op.execute(
        """
        INSERT IGNORE INTO table_name (col1, col2)
        VALUES ("value1", "value2")
        """
    )

def downgrade() -> None:
    op.execute(
        """
        DELETE FROM table_name WHERE col1 = "value1"
        """
    )
```

## Best Practices

- Never modify deployed migrations - create a new one
- Review auto-generated code - Alembic may miss some changes
- Test locally before deploying
- Use `stamp` when you need to mark the database at a revision without running migrations (e.g., when tables already exist)
