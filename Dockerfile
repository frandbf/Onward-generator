# Optional container for the Python pipeline and its checks. Unity and adb stay on the host.
# Build and run through tools/docker/run.sh; the repository is mounted at /work.
FROM python:3.14-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends shellcheck git \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp/home

WORKDIR /work
COPY requirements.lock requirements-mutate.lock ./
RUN python -m pip install --only-binary=:all: -r requirements.lock \
    && python -m pip install -r requirements-mutate.lock

# Editable install against an empty package: at run time the bind mount at /work
# brings the real src/ to the path the install points at. Only pyproject.toml is
# copied, so a code or README change does not rebuild this layer.
COPY pyproject.toml ./
RUN mkdir -p src/onward_generator \
    && touch README.md src/onward_generator/__init__.py \
    && python -m pip install --no-deps -e . \
    && mkdir -p /tmp/home && chmod 1777 /tmp/home

CMD ["onward-generator", "--help"]
