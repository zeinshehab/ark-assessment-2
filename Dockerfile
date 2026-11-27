# syntax=docker/dockerfile:1
# Crosscheck: the API + UI, the MCP server it spawns, and the sandbox it runs tests in, in one image.
#
#   docker build -t crosscheck:dev --build-arg APP_VERSION=$(git rev-parse --short HEAD) .
#
# Base images are pinned by digest (multi-arch indexes: the same digest works on amd64 and arm64), so a rebuild of
# the same commit uses the same bytes. Dependabot proposes digest updates (.github/dependabot.yml).
ARG PYTHON_IMAGE=python:3.13-slim@sha256:7249d5587b78209a006dc4a3b5340678f84ea729239193aa559962cd8c1fce50
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.10@sha256:a3ea1357342c1a4bf9a0c53a3deb1b55379aefa037338d8113d5771efa01774f

FROM ${UV_IMAGE} AS uv

# ---------- build: install the locked dependencies into a virtualenv ----------
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
# --frozen: install exactly what uv.lock says (fail instead of re-resolving); --no-dev: no test/audit tools
RUN uv sync --frozen --no-dev --no-install-project

# ---------- runtime: no build tools, no uv, a non-root user ----------
FROM ${PYTHON_IMAGE} AS runtime
ARG APP_VERSION=dev
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    APP_ENV=production \
    APP_VERSION=${APP_VERSION} \
    DATA_DIR=/data
LABEL org.opencontainers.image.source="https://github.com/zeinshehab/ark-assessment-2" \
      org.opencontainers.image.revision="${APP_VERSION}"

# uid/gid 10001: not root, and not a uid that exists on typical hosts
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin app \
 && mkdir /data && chown app:app /data

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
# Source stays owned by root: the app user can read it but not modify it
COPY reviewer_loop ./reviewer_loop
COPY api ./api
COPY benchmark ./benchmark
COPY standards ./standards
COPY results ./results

USER app
EXPOSE 8000
VOLUME ["/data"]
# Liveness only (is the process answering?). Readiness, which also starts the MCP server, is /readyz: deploy.sh
# checks that before declaring a deployment good.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
CMD ["python", "-m", "api", "--host", "0.0.0.0", "--port", "8000"]
