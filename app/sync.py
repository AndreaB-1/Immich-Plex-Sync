"""Core sync logic: Immich albums → symlinked folder structure."""

import logging
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from immich import ImmichClient

logger = logging.getLogger(__name__)

# Characters not allowed in directory names across common filesystems
_ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|]')
_WHITESPACE = re.compile(r"\s+")


def sanitize_name(name: str) -> str:
    """Strip illegal filesystem characters and collapse whitespace."""
    name = _ILLEGAL_CHARS.sub("", name)
    name = _WHITESPACE.sub(" ", name).strip()
    return name or "Unnamed"


@dataclass
class SyncResult:
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None
    success: bool = False
    error: Optional[str] = None
    albums_found: int = 0
    symlinks_created: int = 0
    symlinks_updated: int = 0
    symlinks_skipped: int = 0
    dirs_removed: int = 0


# Shared state updated by the sync thread
_lock = threading.Lock()
last_result: Optional[SyncResult] = None


def _set_result(result: SyncResult) -> None:
    global last_result
    with _lock:
        last_result = result


def get_last_result() -> Optional[SyncResult]:
    with _lock:
        return last_result


def sync(immich_url: str, api_key: str, output_dir: str) -> SyncResult:
    """Run a full sync and return the result."""
    result = SyncResult()
    _set_result(result)

    try:
        client = ImmichClient(immich_url, api_key)
        _run_sync(client, output_dir, result)
        result.success = True
        logger.info(
            "Sync complete — %d albums, %d created, %d updated, %d skipped, %d dirs removed",
            result.albums_found,
            result.symlinks_created,
            result.symlinks_updated,
            result.symlinks_skipped,
            result.dirs_removed,
        )
    except Exception as exc:  # noqa: BLE001
        result.success = False
        result.error = str(exc)
        logger.error("Sync failed: %s", exc, exc_info=True)
    finally:
        result.finished_at = datetime.now(timezone.utc)
        _set_result(result)

    return result


def _run_sync(client: ImmichClient, output_dir: str, result: SyncResult) -> None:
    os.makedirs(output_dir, exist_ok=True)

    albums = client.get_albums()
    result.albums_found = len(albums)
    logger.info("Found %d albums in Immich", len(albums))

    # Build a map from sanitized dir name → album id to detect collisions
    name_to_id: dict[str, str] = {}
    dir_name_for: dict[str, str] = {}  # album_id → dir name used

    for album in albums:
        album_id: str = album["id"]
        raw_name: str = album.get("albumName", "")
        base = sanitize_name(raw_name)

        # Handle name collisions by appending first 6 chars of album ID
        candidate = base
        if candidate in name_to_id and name_to_id[candidate] != album_id:
            candidate = f"{base} [{album_id[:6]}]"
            logger.warning(
                "Album name collision for '%s' — using '%s' for album %s",
                base,
                candidate,
                album_id,
            )

        name_to_id[candidate] = album_id
        dir_name_for[album_id] = candidate

    # Current set of managed dir names (lower-cased for case-insensitive FS safety)
    current_dir_names = set(dir_name_for.values())

    # Process each album
    for album in albums:
        album_id = album["id"]
        dir_name = dir_name_for[album_id]
        album_dir = os.path.join(output_dir, dir_name)

        logger.info("Processing album '%s' (id=%s)", dir_name, album_id)

        try:
            detail = client.get_album(album_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to fetch album %s detail: %s", album_id, exc)
            continue

        assets: list[dict] = detail.get("assets", [])
        os.makedirs(album_dir, exist_ok=True)
        _sync_album_assets(assets, album_dir, result)

    # Clean up stale directories
    _cleanup_stale_dirs(output_dir, current_dir_names, result)

def _rewrite_path(p: str) -> str:
    """Rewrite symlink target path using env var prefixes."""
    src = os.environ.get("SYMLINK_SOURCE_PREFIX", "").rstrip("/")
    tgt = os.environ.get("SYMLINK_TARGET_PREFIX", "").rstrip("/")
    if src and tgt and p.startswith(src):
        return tgt + p[len(src):]
    return p

def _sync_album_assets(assets: list[dict], album_dir: str, result: SyncResult) -> None:
    """Create/update symlinks for all assets in an album directory."""
    managed_links: set[str] = set()

    for asset in assets:
        original_path: str = asset.get("originalPath", "")
        if not original_path:
            logger.warning("Asset %s has no originalPath, skipping", asset.get("id", "?"))
            continue

        filename = os.path.basename(original_path)
        link_path = os.path.join(album_dir, filename)
        managed_links.add(link_path)

        if not os.path.exists(original_path):
            logger.warning("Source file does not exist, skipping: %s", original_path)
            continue
        symlink_target = _rewrite_path(original_path)
        if os.path.islink(link_path):
            existing_target = os.readlink(link_path)
            if existing_target == symlink_target:
                result.symlinks_skipped += 1
                continue
            # Wrong target — recreate
            os.remove(link_path)
            os.symlink(symlink_target, link_path)
            result.symlinks_updated += 1
            logger.debug("Updated symlink: %s → %s", link_path, original_path)
        elif os.path.exists(link_path):
            # A real file exists at this path — never overwrite
            logger.warning("Real file at symlink path, skipping: %s", link_path)
            result.symlinks_skipped += 1
        else:
            os.symlinksymlink_target, link_path)
            result.symlinks_created += 1
            logger.debug("Created symlink: %s → %s", link_path, original_path)

    # Remove symlinks in this dir that are no longer in the asset list
    for entry in os.scandir(album_dir):
        if entry.path not in managed_links and entry.is_symlink():
            os.remove(entry.path)
            logger.info("Removed stale symlink: %s", entry.path)


def _cleanup_stale_dirs(output_dir: str, current_dir_names: set[str], result: SyncResult) -> None:
    """Remove album directories that no longer correspond to an active album."""
    try:
        existing = [e for e in os.scandir(output_dir) if e.is_dir(follow_symlinks=False)]
    except OSError as exc:
        logger.error("Cannot scan output dir: %s", exc)
        return

    for entry in existing:
        if entry.name in current_dir_names:
            continue

        logger.info("Removing stale album directory: %s", entry.path)
        # Remove only symlinks inside; skip if real files exist
        all_removed = True
        try:
            for item in os.scandir(entry.path):
                if item.is_symlink():
                    os.remove(item.path)
                else:
                    logger.warning(
                        "Non-symlink found in stale dir, leaving directory: %s", item.path
                    )
                    all_removed = False
                    break
        except OSError as exc:
            logger.error("Error cleaning stale dir %s: %s", entry.path, exc)
            continue

        if all_removed:
            try:
                os.rmdir(entry.path)
                result.dirs_removed += 1
                logger.info("Removed stale directory: %s", entry.path)
            except OSError as exc:
                logger.error("Could not remove directory %s: %s", entry.path, exc)
