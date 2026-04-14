"""Entrypoint: APScheduler + Flask web UI for immich-to-plex sync."""

import logging
import os
import sys
import threading
from collections import deque
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask, redirect, render_template, Response, url_for

import sync as sync_module
from sync import sync

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
IMMICH_URL = os.environ["IMMICH_URL"]
IMMICH_API_KEY = os.environ["IMMICH_API_KEY"]
OUTPUT_DIR = os.environ["OUTPUT_DIR"]
SYNC_INTERVAL = os.environ.get("SYNC_INTERVAL", "0 2 * * *")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
WEB_PORT = int(os.environ.get("WEB_PORT", "8585"))

# ---------------------------------------------------------------------------
# Logging — ring-buffer handler so the web UI can show the last N lines
# ---------------------------------------------------------------------------
LOG_BUFFER_SIZE = 500
log_buffer: deque[str] = deque(maxlen=LOG_BUFFER_SIZE)


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        log_buffer.append(self.format(record))


_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

_buf_handler = _BufferHandler()
_buf_handler.setFormatter(_fmt)

_stream_handler = logging.StreamHandler(sys.stdout)
_stream_handler.setFormatter(_fmt)

root_logger = logging.getLogger()
root_logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
root_logger.addHandler(_buf_handler)
root_logger.addHandler(_stream_handler)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sync state
# ---------------------------------------------------------------------------
_sync_lock = threading.Lock()  # Prevent concurrent syncs
_is_syncing = False


def _run_sync() -> None:
    global _is_syncing
    with _sync_lock:
        if _is_syncing:
            logger.warning("Sync already in progress, skipping")
            return
        _is_syncing = True
    try:
        logger.info("Starting sync…")
        sync(IMMICH_URL, IMMICH_API_KEY, OUTPUT_DIR)
    finally:
        with _sync_lock:
            _is_syncing = False


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
scheduler = BackgroundScheduler(daemon=True)

cron_parts = SYNC_INTERVAL.split()
if len(cron_parts) == 5:
    minute, hour, day, month, day_of_week = cron_parts
else:
    logger.warning("Invalid SYNC_INTERVAL '%s', falling back to daily at 02:00", SYNC_INTERVAL)
    minute, hour, day, month, day_of_week = "0", "2", "*", "*", "*"

trigger = CronTrigger(
    minute=minute,
    hour=hour,
    day=day,
    month=month,
    day_of_week=day_of_week,
)
scheduler.add_job(_run_sync, trigger, id="sync", name="Immich → Plex sync")
scheduler.start()

# Run an initial sync in a background thread right away
threading.Thread(target=_run_sync, daemon=True, name="initial-sync").start()

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)


def _next_run_time() -> str:
    job = scheduler.get_job("sync")
    if job and job.next_run_time:
        return job.next_run_time.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return "N/A"


@app.route("/")
def index() -> str:
    result = sync_module.get_last_result()
    lines = list(log_buffer)[-50:]

    last_sync_time = "Never"
    last_sync_status = "—"
    last_sync_error = None
    albums_found = 0
    symlinks_created = 0
    symlinks_updated = 0

    if result:
        if result.finished_at:
            last_sync_time = result.finished_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        else:
            last_sync_time = result.started_at.strftime("%Y-%m-%d %H:%M:%S UTC") + " (running…)"
        last_sync_status = "Success" if result.success else "Error"
        last_sync_error = result.error
        albums_found = result.albums_found
        symlinks_created = result.symlinks_created
        symlinks_updated = result.symlinks_updated

    return render_template(
        "index.html",
        immich_url=IMMICH_URL,
        output_dir=OUTPUT_DIR,
        sync_interval=SYNC_INTERVAL,
        last_sync_time=last_sync_time,
        last_sync_status=last_sync_status,
        last_sync_error=last_sync_error,
        next_sync_time=_next_run_time(),
        albums_found=albums_found,
        symlinks_created=symlinks_created,
        symlinks_updated=symlinks_updated,
        log_lines=lines,
        is_syncing=_is_syncing,
    )


@app.route("/trigger", methods=["POST"])
def trigger() -> Response:
    threading.Thread(target=_run_sync, daemon=True, name="manual-sync").start()
    return redirect(url_for("index"))


@app.route("/logs")
def logs() -> Response:
    content = "\n".join(list(log_buffer)[-200:])
    return Response(content, mimetype="text/plain")


if __name__ == "__main__":
    logger.info(
        "immich-to-plex starting — URL=%s OUTPUT=%s INTERVAL=%s PORT=%d",
        IMMICH_URL,
        OUTPUT_DIR,
        SYNC_INTERVAL,
        WEB_PORT,
    )
    app.run(host="0.0.0.0", port=WEB_PORT)
