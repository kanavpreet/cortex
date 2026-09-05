#!/bin/bash
set -e

# Create a new migration with SQL files and Alembic version
# Usage: ./create_migration.sh "description"

if [ $# -eq 0 ]; then
    echo "Usage: ./create_migration.sh \"description\""
    echo "Example: ./create_migration.sh \"add users table\""
    exit 1
fi

DESCRIPTION="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSIONS_DIR="$SCRIPT_DIR/../migrator/alembic/versions"
MIGRATIONS_DIR="$SCRIPT_DIR/../common/migrations"

# Get the next migration number
get_next_number() {
    local max_num=0

    if [ -d "$VERSIONS_DIR" ]; then
        for file in "$VERSIONS_DIR"/*.py; do
            if [ -f "$file" ]; then
                filename=$(basename "$file")
                if [[ $filename =~ ^([0-9]{6})_ ]]; then
                    num=${BASH_REMATCH[1]}
                    if [ $((10#$num)) -gt $max_num ]; then
                        max_num=$((10#$num))
                    fi
                fi
            fi
        done
    fi

    echo $(printf "%06d" $((max_num + 1)))
}

# Convert description to snake_case filename
to_snake_case() {
    echo "$1" | tr '[:upper:]' '[:lower:]' | tr ' ' '_' | sed 's/[^a-z0-9_]//g'
}

NEXT_NUM=$(get_next_number)
SNAKE_DESC=$(to_snake_case "$DESCRIPTION")
MIGRATION_NAME="${NEXT_NUM}_${SNAKE_DESC}"

echo "Creating migration: $MIGRATION_NAME"

# Create SQL files
UP_SQL="$MIGRATIONS_DIR/${MIGRATION_NAME}.up.sql"
DOWN_SQL="$MIGRATIONS_DIR/${MIGRATION_NAME}.down.sql"

mkdir -p "$MIGRATIONS_DIR"

cat > "$UP_SQL" << EOF
-- Migration: $DESCRIPTION
-- Created: $(date -u +"%Y-%m-%d %H:%M:%S UTC")

-- Add your SQL here

EOF

cat > "$DOWN_SQL" << EOF
-- Migration: $DESCRIPTION (Rollback)
-- Created: $(date -u +"%Y-%m-%d %H:%M:%S UTC")

-- Add your rollback SQL here

EOF

echo "✓ Created SQL files:"
echo "  - $UP_SQL"
echo "  - $DOWN_SQL"

# Create Alembic version
cd "$SCRIPT_DIR/../migrator"
uv run alembic revision -m "$SNAKE_DESC" --rev-id "$NEXT_NUM" > /dev/null

echo "✓ Created Alembic migration:"
echo "  - $VERSIONS_DIR/${MIGRATION_NAME}.py"
echo ""
echo "Next steps:"
echo "  1. Edit $UP_SQL"
echo "  2. Edit $DOWN_SQL"
echo "  3. Run migrations: docker-compose up migrator"
