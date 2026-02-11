#!/bin/sh
set -e

echo "=== DB Init: Waiting for PostgreSQL to be ready ==="
until pg_isready -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" 2>/dev/null; do
  echo "Waiting for PostgreSQL..."
  sleep 2
done

echo "=== DB Init: Running SQL scripts ==="
for f in /scripts/*.sql; do
  echo "Executing: $f"
  psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -f "$f" || true
done

echo "=== DB Init: Complete ==="
