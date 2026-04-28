#!/usr/bin/env sh

# This script resets the database schema by dropping and recreating the public schema.
# It is intended to be used in development environments for testing purposes.

# Usage:
#   ./reset-db.sh [path_to_env_file] [allowed_db_name]
#   - path_to_env_file: Optional path to the .env file containing DB credentials (default: ./.env)
#   - allowed_db_name: Optional name of the database that is allowed to be reset (default: st_pitch_db)

set -eu

ENV_FILE="${1:-./.env}"
ALLOWED_DB_NAME="${2:-st_pitch_db}"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: env file not found: $ENV_FILE" >&2
  exit 1
fi

# Load env vars from file
set -a
. "$ENV_FILE"
set +a

# Required variables
for v in DB_NAME DB_USER DB_PASSWORD DB_HOST DB_PORT; do
  eval "val=\${$v:-}"
  if [ -z "$val" ]; then
    echo "ERROR: missing required variable: $v" >&2
    exit 1
  fi
done

# Safety guard: refuse if DB name is not the expected one
if [ "$DB_NAME" != "$ALLOWED_DB_NAME" ]; then
  echo "ERROR: refusing to reset DB." >&2
  echo "       expected DB_NAME=$ALLOWED_DB_NAME, but got DB_NAME=$DB_NAME" >&2
  exit 1
fi

# Escape double quotes in role name for SQL identifier safety
DB_USER_SQL=$(printf '%s' "$DB_USER" | sed 's/"/""/g')

# Step 1: Drop and recreate schema (as app user)
PGPASSWORD="$DB_PASSWORD" psql \
  -h "$DB_HOST" \
  -p "$DB_PORT" \
  -U "$DB_USER" \
  -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 \
  -c "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO \"$DB_USER_SQL\"; GRANT ALL ON SCHEMA public TO public;"

# Step 2: Create PostGIS extension (requires superuser)
# DB_POST and DB_PORT are not used here because we connect via local socket as postgres user
sudo -u postgres psql \
  -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 \
  -c "CREATE EXTENSION IF NOT EXISTS postgis;"

# Step 3: Verify geometry type exists (PostGIS ready)
GEOM_OK=$(PGPASSWORD="$DB_PASSWORD" psql \
  -h "$DB_HOST" \
  -p "$DB_PORT" \
  -U "$DB_USER" \
  -d "$DB_NAME" \
  -tA \
  -c "SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'geometry');")

if [ "$GEOM_OK" != "t" ]; then
  echo "ERROR: PostGIS geometry type is not available after reset." >&2
  echo "       Check PostGIS installation and user privileges for CREATE EXTENSION." >&2
  exit 1
fi

echo "OK: schema reset completed for DB_NAME=$DB_NAME"

# Step 4: Remove uploaded CSV files
UPLOAD_DIR="$(dirname "$0")/uploads"
if [ -d "$UPLOAD_DIR" ]; then
  CSV_COUNT=$(find "$UPLOAD_DIR" -maxdepth 1 -name "*.csv" | wc -l)
  if [ "$CSV_COUNT" -gt 0 ]; then
    find "$UPLOAD_DIR" -maxdepth 1 -name "*.csv" -delete
    echo "OK: deleted $CSV_COUNT CSV file(s) from $UPLOAD_DIR"
  else
    echo "INFO: no CSV files found in $UPLOAD_DIR"
  fi
else
  echo "WARNING: upload directory not found: $UPLOAD_DIR" >&2
fi
