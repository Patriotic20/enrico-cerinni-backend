#!/bin/bash
# Ensure all output is flushed immediately — critical for Railway log visibility
export PYTHONUNBUFFERED=1

set -euo pipefail

echo "Starting Enrico Backend..." >&2

# Use Railway's PORT if available, fallback to 8000
APP_PORT=${PORT:-8000}

# ---------------------------------------------------------------------------
# Step 1: Validate DATABASE_URL is present
# ---------------------------------------------------------------------------
echo "Step 1: Validating DATABASE_URL..." >&2

if [ -z "${DATABASE_URL:-}" ]; then
    echo "❌ FATAL: DATABASE_URL environment variable is not set. Cannot start." >&2
    exit 1
fi

echo "✅ DATABASE_URL is set." >&2

# ---------------------------------------------------------------------------
# Step 2: Wait for the database to accept connections
# ---------------------------------------------------------------------------
echo "Step 2: Checking database connection..." >&2

uv run python - <<'PYEOF'
import os
import sys
import time
import psycopg2

db_url = os.environ["DATABASE_URL"]

# Normalise legacy postgres:// scheme used by some Railway connection strings
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

max_attempts = 30
wait_seconds = 2

print(f"Connecting to database (up to {max_attempts * wait_seconds}s)...", flush=True)

for attempt in range(1, max_attempts + 1):
    try:
        conn = psycopg2.connect(db_url, connect_timeout=5)
        conn.close()
        print("✅ Database connection successful!", flush=True)
        sys.exit(0)
    except psycopg2.OperationalError as e:
        print(
            f"⏳ Attempt {attempt}/{max_attempts}: database not ready "
            f"(OperationalError: {e}), retrying in {wait_seconds}s...",
            file=sys.stderr,
            flush=True,
        )
    except Exception as e:
        print(
            f"❌ Unexpected error on attempt {attempt}/{max_attempts}: "
            f"{type(e).__name__}: {e}",
            file=sys.stderr,
            flush=True,
        )

    time.sleep(wait_seconds)

print(
    f"❌ FATAL: Database did not become ready after {max_attempts} attempts "
    f"({max_attempts * wait_seconds}s). Aborting.",
    file=sys.stderr,
    flush=True,
)
sys.exit(1)
PYEOF

# ---------------------------------------------------------------------------
# Step 3: Run Alembic migrations — failure is fatal
# ---------------------------------------------------------------------------
echo "Step 3: Running database migrations..." >&2

if uv run alembic upgrade head; then
    echo "✅ Migrations completed successfully!" >&2
else
    echo "❌ FATAL: Alembic migrations failed. Aborting startup to prevent schema mismatch." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 4: Start the FastAPI application
# ---------------------------------------------------------------------------
echo "Step 4: Starting FastAPI server on port $APP_PORT..." >&2

exec uv run uvicorn main:app \
    --host 0.0.0.0 \
    --port "$APP_PORT" \
    --proxy-headers \
    --forwarded-allow-ips='*' \
    --log-level info
