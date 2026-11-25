# TrueNAS backup: powers the backup TrueNAS on, runs the replication through
# the primary's API, powers it off again, and alerts Home Assistant on failure.

# ---------------------------------------------------------------------------
# runtime: everything the backup cycle needs
# ---------------------------------------------------------------------------
FROM python:3.15-alpine AS runtime

# Set by the build workflow from the VERSION file.
ARG VERSION=dev

LABEL org.opencontainers.image.title="Docker-TrueNAS_Backup" \
      org.opencontainers.image.description="Weekly power-on, ZFS replication and shutdown of a backup TrueNAS" \
      org.opencontainers.image.version="${VERSION}"

# tzdata: BACKUP_SCHEDULE runs in the container's TZ.
# ipmitool: powers the backup box on and off through its BMC.
RUN apk add --no-cache tzdata ipmitool

WORKDIR /opt/truenas-backup

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY VERSION .
COPY app/ ./

# `docker exec truenas-backup backup` runs one cycle by hand.
RUN printf '#!/bin/sh\nexec python /opt/truenas-backup/backup.py "$@"\n' > /usr/local/bin/backup \
    && chmod 0755 /usr/local/bin/backup

# Log lines appear as they are written; no .pyc files in the image.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Unhealthy when the scheduler has stopped or the settings are wrong
# (see healthcheck.py). A failed backup is reported to Home Assistant instead.
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python", "/opt/truenas-backup/healthcheck.py"]

CMD ["python", "-u", "/opt/truenas-backup/scheduler.py"]

# ---------------------------------------------------------------------------
# test: runs the unit tests against the runtime image. The build workflow
# builds this stage first; a failing test stops the build.
#   docker build --target test .
# ---------------------------------------------------------------------------
FROM runtime AS test

COPY tests/ ./tests/
RUN python -m unittest discover -s tests -v

# ---------------------------------------------------------------------------
# The published image (the last stage is the default build target).
# ---------------------------------------------------------------------------
FROM runtime
