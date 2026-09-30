FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
# Every dependency ships as a wheel (psycopg2-binary, bcrypt, cryptography), so
# no compiler or libpq-dev is needed. uv installs from the pinned lock, prod-only.
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
# startup.sh calls python/alembic/uvicorn straight from the venv: `uv run`
# would re-sync (and pull the dev group) on every boot.
ENV PATH="/app/.venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first: this layer stays cached until pyproject.toml / uv.lock change,
# so a code-only deploy doesn't reinstall every package.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev && chmod +x /app/startup.sh

EXPOSE ${PORT:-8000}

CMD ["/bin/sh", "/app/startup.sh"]
