# Optional container for the Python pipeline and its checks. Unity and adb stay on the host.
# Build and run through tools/docker/run.sh; the repository is mounted at /work.
FROM python:3.14-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends shellcheck \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp/home

WORKDIR /work
COPY requirements.lock ./
RUN python -m pip install --only-binary=:all: -r requirements.lock

# Editable install: at run time the bind mount at /work replaces these files,
# and the installed path still points at /work/src.
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-deps -e . \
    && mkdir -p /tmp/home && chmod 1777 /tmp/home

CMD ["onward-generator", "--help"]
