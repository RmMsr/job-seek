# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:python3.12-trixie-slim AS builder

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Deps layer cached separately from app code.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM ghcr.io/astral-sh/uv:python3.12-trixie-slim

WORKDIR /app
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=0

COPY --from=builder /app/.venv /app/.venv

# PLAYWRIGHT_BROWSERS_PATH=0 installs into the venv, not ~/.cache, so it's
# found regardless of runtime uid.
RUN playwright install --with-deps chromium

COPY app ./app

# data/ is the mount point for all per-instance state; a bind mount would
# shadow anything baked in there, so ship the default config elsewhere and
# have the entrypoint copy it in on first run.
COPY config-container-template.toml defaults/config.toml
RUN ln --symbolic data/config.toml config.toml

COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh

# Non-root uid/gid 1000, matching Podman's --userns=keep-id:uid=1000,gid=1000
# (see README) so bind-mounted data/ stays owned by the host user.
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --no-create-home --home-dir /app appuser && \
    mkdir --parents /app/data && \
    chown appuser:appuser /app/data
USER appuser

VOLUME /app/data
EXPOSE 8000

ENTRYPOINT ["entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
