# The marketdata image (D-134): the API with /health, /v1, /proof, /mcp and the web UI. The
# builder stage installs the locked production dependencies and the package into a virtualenv;
# the runtime stage copies only that virtualenv and runs as a user that can't log in. The worker
# and the scheduler run from the same image with another command (docker-compose.yml, D-137).
ARG PYTHON_IMAGE=python:3.15-slim-trixie

FROM ${PYTHON_IMAGE} AS builder
ENV PDM_CHECK_UPDATE=false PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
# The PDM that writes pdm.lock. unearth 0.19 moved from httpx to httpx2, which PDM 2.29.3's
# index client doesn't expect ("PDMPyPIClient is not an instance of Client"), so it stays on the
# 0.18 that PDM runs with on the dev machine.
RUN pip install pdm==2.29.3 unearth==0.18.3
WORKDIR /app
COPY pyproject.toml pdm.lock README.md ./
# Dependencies first, so a code change doesn't reinstall them. --check fails on a stale lock, as
# it does in CI.
RUN pdm install --check --prod --no-self
COPY marketdata/ marketdata/
# The package itself as a wheel, with its templates and static files, not a link to the source.
RUN pdm install --check --prod --no-editable

FROM ${PYTHON_IMAGE}
ENV PATH=/app/.venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin marketdata
WORKDIR /app
COPY --from=builder /app/.venv/ .venv/
USER marketdata
EXPOSE 8000
CMD ["uvicorn", "marketdata.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
