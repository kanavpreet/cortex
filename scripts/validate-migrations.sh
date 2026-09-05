#!/bin/bash
# Pre-commit hook to validate SQL migration files
# Checks for best practices in migration files

set -e

MIGRATION_DIR="common/migrations"
ERRORS=0

# ANSI color codes
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

echo "🔍 Validating migration files..."

# Determine which files to validate
# If CI_MODE is set, validate all migration files
# Otherwise, validate only staged files (pre-commit hook behavior)
if [ "${CI_MODE:-}" = "true" ]; then
    echo "Running in CI mode - validating all migration files"
    STAGED_MIGRATIONS=$(find "${MIGRATION_DIR}" -type f -name "*.sql" | sort)
else
    echo "Running in pre-commit mode - validating staged files only"
    STAGED_MIGRATIONS=$(git diff --cached --name-only --diff-filter=ACM | grep "^${MIGRATION_DIR}/.*\.sql$" || true)
fi

if [ -z "$STAGED_MIGRATIONS" ]; then
    echo "✅ No migration files to validate"
    exit 0
fi

echo "Found $(echo "$STAGED_MIGRATIONS" | wc -l | tr -d ' ') migration file(s) to validate"
echo ""

# Validation functions
validate_naming_convention() {
    local file=$1
    local basename=$(basename "$file")

    # Check naming pattern: {6-digit-number}_{description}.{up|down}.sql
    if ! [[ "$basename" =~ ^[0-9]{6}_[a-z0-9_]+\.(up|down)\.sql$ ]]; then
        echo -e "${RED}❌ FAIL${NC}: Invalid naming convention"
        echo "   File: $file"
        echo "   Expected: NNNNNN_description_with_underscores.{up|down}.sql"
        echo "   Example: 000013_add_user_preferences_table.up.sql"
        return 1
    fi
    return 0
}

validate_paired_files() {
    local file=$1
    local basename=$(basename "$file")

    # Check if both .up.sql and .down.sql exist
    if [[ "$basename" =~ \.up\.sql$ ]]; then
        local down_file="${file%.up.sql}.down.sql"
        if [ ! -f "$down_file" ]; then
            echo -e "${RED}❌ FAIL${NC}: Missing corresponding .down.sql file"
            echo "   File: $file"
            echo "   Expected: $down_file"
            echo "   Both .up.sql and .down.sql files are required"
            return 1
        fi
    elif [[ "$basename" =~ \.down\.sql$ ]]; then
        local up_file="${file%.down.sql}.up.sql"
        if [ ! -f "$up_file" ]; then
            echo -e "${RED}❌ FAIL${NC}: Missing corresponding .up.sql file"
            echo "   File: $file"
            echo "   Expected: $up_file"
            echo "   Both .up.sql and .down.sql files are required"
            return 1
        fi
    fi
    return 0
}

count_sql_statements() {
    local file=$1
    local content=$(cat "$file")

    # Remove comments (-- style and /* */ style)
    content=$(echo "$content" | sed 's/--.*$//' | sed 's:/\*.*\*/::g')

    # Count semicolons that are not in strings
    # This is a simple heuristic - counts lines with semicolons not followed by more SQL
    local count=$(echo "$content" | grep -c ";" || echo "0")

    echo "$count"
}

validate_single_operation() {
    local file=$1
    local basename=$(basename "$file")

    # Skip down migrations - they can be simple DROP statements
    if [[ "$basename" =~ \.down\.sql$ ]]; then
        return 0
    fi

    # Read file content
    local content=$(cat "$file")

    # Remove comments and empty lines
    local clean_content=$(echo "$content" | sed 's/--.*$//' | sed 's:/\*.*\*/::g' | sed '/^\s*$/d')

    # Check for multiple distinct operations (heuristic)
    # Use word boundaries and more specific patterns to avoid false matches
    local create_count=$(echo "$clean_content" | grep -ci "^\s*CREATE TABLE" 2>/dev/null || echo "0")
    local alter_count=$(echo "$clean_content" | grep -ci "^\s*ALTER TABLE" 2>/dev/null || echo "0")
    local insert_count=$(echo "$clean_content" | grep -ci "^\s*INSERT INTO" 2>/dev/null || echo "0")
    local update_count=$(echo "$clean_content" | grep -ci "^\s*UPDATE\s" 2>/dev/null || echo "0")
    local drop_count=$(echo "$clean_content" | grep -ci "^\s*DROP TABLE" 2>/dev/null || echo "0")
    local index_count=$(echo "$clean_content" | grep -ci "^\s*CREATE INDEX" 2>/dev/null || echo "0")

    # Strip whitespace and ensure valid integers
    create_count=$(echo "$create_count" | tr -d '[:space:]')
    alter_count=$(echo "$alter_count" | tr -d '[:space:]')
    insert_count=$(echo "$insert_count" | tr -d '[:space:]')
    update_count=$(echo "$update_count" | tr -d '[:space:]')
    drop_count=$(echo "$drop_count" | tr -d '[:space:]')
    index_count=$(echo "$index_count" | tr -d '[:space:]')

    create_count=${create_count:-0}
    alter_count=${alter_count:-0}
    insert_count=${insert_count:-0}
    update_count=${update_count:-0}
    drop_count=${drop_count:-0}
    index_count=${index_count:-0}

    local total_operations=$((create_count + alter_count + insert_count + update_count + drop_count + index_count))

    if [ "$total_operations" -gt 1 ]; then
        echo -e "${RED}❌ FAIL${NC}: Multiple operations detected (found $total_operations)"
        echo "   File: $file"
        echo "   Best practice: One logical operation per migration"
        echo "   Found: CREATE TABLE ($create_count), ALTER TABLE ($alter_count), INSERT ($insert_count), UPDATE ($update_count), DROP ($drop_count), CREATE INDEX ($index_count)"
        echo "   Solution: Split into separate migration files"
        echo ""
        return 1
    fi
    return 0
}

validate_idempotency() {
    local file=$1
    local basename=$(basename "$file")
    local content=$(cat "$file")

    # Check for idempotent keywords in up migrations
    if [[ "$basename" =~ \.up\.sql$ ]]; then
        if echo "$content" | grep -qi "CREATE TABLE"; then
            if ! echo "$content" | grep -qi "IF NOT EXISTS"; then
                echo -e "${YELLOW}⚠️  WARN${NC}: CREATE TABLE without IF NOT EXISTS"
                echo "   File: $file"
                echo "   Best practice: Use 'CREATE TABLE IF NOT EXISTS' for idempotency"
                echo ""
                return 1
            fi
        fi

        if echo "$content" | grep -qi "ALTER TABLE.*ADD COLUMN"; then
            if ! echo "$content" | grep -qi "IF NOT EXISTS"; then
                echo -e "${YELLOW}⚠️  WARN${NC}: ADD COLUMN without IF NOT EXISTS"
                echo "   File: $file"
                echo "   Best practice: Use 'ADD COLUMN IF NOT EXISTS' for idempotency"
                echo ""
                return 1
            fi
        fi

        if echo "$content" | grep -qi "CREATE INDEX"; then
            if ! echo "$content" | grep -qi "IF NOT EXISTS"; then
                echo -e "${YELLOW}⚠️  WARN${NC}: CREATE INDEX without IF NOT EXISTS"
                echo "   File: $file"
                echo "   Best practice: Use 'CREATE INDEX IF NOT EXISTS' for idempotency"
                echo ""
                return 1
            fi
        fi
    fi

    # Check for idempotent keywords in down migrations
    if [[ "$basename" =~ \.down\.sql$ ]]; then
        if echo "$content" | grep -qi "DROP TABLE"; then
            if ! echo "$content" | grep -qi "IF EXISTS"; then
                echo -e "${YELLOW}⚠️  WARN${NC}: DROP TABLE without IF EXISTS"
                echo "   File: $file"
                echo "   Best practice: Use 'DROP TABLE IF EXISTS' for idempotency"
                echo ""
                return 1
            fi
        fi

        if echo "$content" | grep -qi "DROP INDEX"; then
            if ! echo "$content" | grep -qi "IF EXISTS"; then
                echo -e "${YELLOW}⚠️  WARN${NC}: DROP INDEX without IF EXISTS"
                echo "   File: $file"
                echo "   Best practice: Use 'DROP INDEX IF EXISTS' for idempotency"
                echo ""
                return 1
            fi
        fi
    fi

    return 0
}

validate_not_empty() {
    local file=$1
    local content=$(cat "$file" | sed 's/--.*$//' | sed 's:/\*.*\*/::g' | sed '/^\s*$/d')

    if [ -z "$content" ]; then
        echo -e "${RED}❌ FAIL${NC}: Migration file is empty"
        echo "   File: $file"
        echo "   Migration files must contain SQL statements"
        return 1
    fi
    return 0
}

validate_migration_numbering() {
    echo "Checking migration numbering sequence..."

    # Get all migration files and extract their numbers
    local all_migrations=$(find "${MIGRATION_DIR}" -type f -name "*.up.sql" | sort)

    if [ -z "$all_migrations" ]; then
        echo "No migrations found to check numbering"
        return 0
    fi

    local -a migration_numbers=()
    local -a migration_files=()

    # Extract migration numbers
    for file in $all_migrations; do
        local basename=$(basename "$file")
        local number=$(echo "$basename" | grep -o "^[0-9]\{6\}")
        migration_numbers+=("$number")
        migration_files+=("$file")
    done

    # Check for duplicates
    local duplicates=$(printf '%s\n' "${migration_numbers[@]}" | sort | uniq -d)
    if [ -n "$duplicates" ]; then
        echo -e "${RED}❌ FAIL${NC}: Duplicate migration numbers found"
        for dup in $duplicates; do
            echo "   Duplicate number: $dup"
            for i in "${!migration_numbers[@]}"; do
                if [ "${migration_numbers[$i]}" = "$dup" ]; then
                    echo "      - ${migration_files[$i]}"
                fi
            done
        done
        echo "   Solution: Renumber migrations to ensure unique sequential numbers"
        return 1
    fi

    # Check for sequential ordering and gaps
    local prev_number=0
    local has_gaps=0
    local gap_messages=""

    for i in "${!migration_numbers[@]}"; do
        local current_number="${migration_numbers[$i]}"
        local current_file="${migration_files[$i]}"

        # Remove leading zeros for arithmetic
        current_number=$((10#$current_number))

        if [ $prev_number -eq 0 ]; then
            # First migration - check if it starts at 000001
            if [ $current_number -ne 1 ]; then
                echo -e "${YELLOW}⚠️  WARN${NC}: First migration does not start at 000001"
                echo "   Found: $(printf "%06d" $current_number)"
                echo "   Expected: 000001"
            fi
        else
            local expected_number=$((prev_number + 1))

            if [ $current_number -ne $expected_number ]; then
                has_gaps=1
                gap_messages="${gap_messages}\n   Gap detected: Expected $(printf "%06d" $expected_number), found $(printf "%06d" $current_number)"
                gap_messages="${gap_messages}\n      After: ${migration_files[$((i-1))]}"
                gap_messages="${gap_messages}\n      Before: ${current_file}"
            fi
        fi

        prev_number=$current_number
    done

    if [ $has_gaps -eq 1 ]; then
        echo -e "${RED}❌ FAIL${NC}: Migration numbering has gaps"
        echo -e "$gap_messages"
        echo "   Solution: Migrations must be numbered sequentially without gaps"
        return 1
    fi

    echo -e "${GREEN}✅ Migration numbering is correct (${#migration_numbers[@]} migrations, sequential from $(printf "%06d" ${migration_numbers[0]}) to $(printf "%06d" ${migration_numbers[-1]})${NC}"
    return 0
}

# Run global validation for migration numbering
echo ""
if ! validate_migration_numbering; then
    ERRORS=$((ERRORS + 1))
fi
echo ""

# Run validations on each staged migration file
for file in $STAGED_MIGRATIONS; do
    echo "Validating: $file"

    # Critical validations (fail commit)
    if ! validate_naming_convention "$file"; then
        ERRORS=$((ERRORS + 1))
    fi

    if ! validate_not_empty "$file"; then
        ERRORS=$((ERRORS + 1))
    fi

    if ! validate_paired_files "$file"; then
        ERRORS=$((ERRORS + 1))
    fi

    if ! validate_single_operation "$file"; then
        ERRORS=$((ERRORS + 1))
    fi

    # Warning validations (don't fail commit, just warn)
    validate_idempotency "$file" || true

    echo ""
done

# Summary
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ $ERRORS -eq 0 ]; then
    echo -e "${GREEN}✅ All migration validations passed!${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    exit 0
else
    echo -e "${RED}❌ Migration validation failed with $ERRORS error(s)${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "Please fix the errors above before committing."
    echo "See _infra/docs/operations/db-management.md for migration best practices."
    exit 1
fi
