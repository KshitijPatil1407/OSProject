# Semantic File Search Agent — agent #7 of the Agentic Shell
#
# Gives the agent a known-good Linux userspace regardless of what the host is
# running. Useful for three things: a teammate on Windows or macOS, a distro
# whose Python keeps moving underneath you, and a reproducible demo for the
# course review.
#
# Build:
#   docker build -t sfs-agent .
#
# See README.md § Running in Docker for the run commands — in particular, the
# agent searches whatever filesystem it can see, so searching your real files
# requires a bind mount.

FROM python:3.13-slim

# ripgrep  — the fast content-search backend (a pure-Python fallback exists,
#            but it is 10-50x slower)
# file     — backs the identify_file tool (degrades gracefully if absent)
RUN apt-get update \
 && apt-get install --no-install-recommends -y ripgrep file \
 && rm -rf /var/lib/apt/lists/*

# Match the host UID so a bind-mounted home directory is readable. Override at
# build time if your UID is not 1000:  --build-arg UID=$(id -u)
ARG UID=1000
ARG GID=1000
RUN groupadd --gid "$GID" agent 2>/dev/null || true \
 && useradd --uid "$UID" --gid "$GID" --create-home --shell /bin/bash agent

WORKDIR /app

# Dependencies first, so edits to the source do not invalidate the pip layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY semantic_search/ ./semantic_search/
COPY tests/ ./tests/
COPY demo.py .

# The agent is read-only by design; running unprivileged makes that structural
# rather than merely intended.
USER agent

# Default search root inside the container. Bind-mount the host directory here.
ENV SFS_ALLOWED_ROOTS=/mnt/host \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# No API key needed for this, so `docker run sfs-agent` proves the image works.
CMD ["python", "tests/test_tools.py"]
