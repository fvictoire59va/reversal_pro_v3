#!/bin/sh
set -e

echo "=== DB Migrations: Waiting for PostgreSQL to be ready ==="
until pg_isready -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" 2>/dev/null; do
  echo "Waiting for PostgreSQL..."
  sleep 2
done

echo "=== DB Migrations: Ensuring schema_version table exists ==="
psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -v ON_ERROR_STOP=1 <<'EOF'
CREATE TABLE IF NOT EXISTS schema_version (
    version     TEXT    PRIMARY KEY,
    script_name TEXT    NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
EOF

echo "=== DB Migrations: Running pending SQL scripts ==="
ERRORS=0
for f in /scripts/*.sql; do
  SCRIPT_NAME=$(basename "$f")

  # Check if this migration was already applied
  ALREADY=$(psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" \
    -tAc "SELECT 1 FROM schema_version WHERE script_name = '$SCRIPT_NAME'" 2>/dev/null)

  if [ "$ALREADY" = "1" ]; then
    echo "  SKIP (already applied): $SCRIPT_NAME"
    continue
  fi

  echo "  APPLY: $SCRIPT_NAME"
  if psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -v ON_ERROR_STOP=1 -f "$f"; then
    # Record successful migration
    psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -v ON_ERROR_STOP=1 \
      -c "INSERT INTO schema_version (version, script_name) VALUES ('$SCRIPT_NAME', '$SCRIPT_NAME')"
    echo "  OK: $SCRIPT_NAME"
  else
    echo "  FAILED: $SCRIPT_NAME — aborting migrations"
    ERRORS=$((ERRORS + 1))
    break
  fi
done

if [ "$ERRORS" -gt 0 ]; then
  echo "=== DB Migrations: FAILED (${ERRORS} error(s)) ==="
  exit 1
fi

echo "=== DB Migrations: Complete ==="
