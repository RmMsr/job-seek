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

ARG VERSION=dev
ARG BUILD_DATE=
WORKDIR /app
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=0 \
    APP_VERSION=${VERSION} \
    APP_BUILD_DATE=${BUILD_DATE}

COPY --from=builder /app/.venv /app/.venv

# PLAYWRIGHT_BROWSERS_PATH=0 installs into the venv, not ~/.cache, so it's
# found regardless of runtime uid.
RUN playwright install --with-deps chromium

# doc-write (AGPL, invoked only as a subprocess) for per-job CV rendering, plus
# the system libraries WeasyPrint needs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libffi8 \
        libjpeg62-turbo libgdk-pixbuf-2.0-0 \
        fonts-dejavu-core \
        fonts-noto-core fonts-noto-extra \
        fonts-roboto fonts-roboto-slab \
        fonts-open-sans fonts-source-sans-3 \
        fonts-ibm-plex \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -fv \
    && uv pip install --python /app/.venv/bin/python \
        "doc-write @ git+https://gitlab.com/RmMsr/doc-write-mcp.git"

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
