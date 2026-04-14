# immich-to-plex

A lightweight Docker container that syncs [Immich](https://immich.app/) albums to a symlinked folder structure so that Plex (or any media server) can browse them as a photo/video library.

No files are copied — only symlinks are created, so there is zero extra disk usage.

---

## Prerequisites

- **Immich** running and accessible over the network
- **Plex** (or another media server) running and able to read from a shared path
- Both Immich and immich-to-plex must be able to **access the same photo files on disk** — either on the same host or via a shared NFS/SMB mount

---

## Quick Start

```bash
git clone https://github.com/andreab-1/immich-plex-sync.git
cd immich-plex-sync
cp .env.example .env
# Edit .env with your values
nano docker-compose.yml   # adjust volume paths
docker compose up -d
```

Open `http://<your-host>:8585` to see the status UI.

---

## How to get an Immich API key

1. Open Immich in your browser
2. Click your avatar → **Account Settings**
3. Scroll to **API Keys** → **New API Key**
4. Copy the key and paste it into `IMMICH_API_KEY`

---

## Volume mapping

The container needs **two mounts**:

| Mount | Purpose |
|---|---|
| Immich library (read-only) | So symlinks created inside the container resolve to real files |
| Output directory | Where album folders full of symlinks are written |

```yaml
volumes:
  - /mnt/photos/immich:/photos:ro   # same path Immich uses internally
  - /mnt/photos/plex-albums:/output # Plex will read from here
```

**Critical:** The left-hand (host) path of the Immich library mount must match the path that `asset.originalPath` returns from the Immich API. If Immich stores files under `/mnt/photos/immich/` and returns paths like `/mnt/photos/immich/2024/...`, mount that same path at the same location inside immich-to-plex.

If the paths differ, symlinks will be created but will be broken (dangling). Check with `ls -la` inside the container and `docker exec immich-to-plex readlink -f /output/MyAlbum/photo.jpg`.

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `IMMICH_URL` | ✅ | — | Base URL of Immich, e.g. `http://192.168.1.10:2283` |
| `IMMICH_API_KEY` | ✅ | — | Immich API key |
| `OUTPUT_DIR` | ✅ | — | Path inside container where album folders are created |
| `SYNC_INTERVAL` | ❌ | `0 2 * * *` | Cron expression for sync schedule |
| `LOG_LEVEL` | ❌ | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `WEB_PORT` | ❌ | `8585` | Port for the status web UI |

---

## Sync schedule

`SYNC_INTERVAL` accepts standard 5-field cron syntax:

```
┌───── minute (0-59)
│ ┌───── hour (0-23)
│ │ ┌───── day of month (1-31)
│ │ │ ┌───── month (1-12)
│ │ │ │ ┌───── day of week (0-6, Sunday=0)
│ │ │ │ │
0 2 * * *   →  every day at 02:00
0 */6 * * * →  every 6 hours
*/30 * * * * →  every 30 minutes
```

An initial sync also runs immediately at container startup.

---

## Adding the output folder to Plex

1. Open Plex → **Settings** → **Libraries** → **Add Library**
2. Choose **Photos**
3. Click **Browse for media folder** and select the **host path** on the left side of your output volume mount (e.g. `/mnt/photos/plex-albums`)
4. Save and let Plex scan

After each sync, trigger a Plex library scan from **Libraries → ⋮ → Scan Library Files** to pick up new albums immediately.

---

## Web UI

- **`/`** — Status page: config, last/next sync time, stats, log tail, Sync Now button
- **`/trigger`** — POST to start an immediate sync
- **`/logs`** — Last 200 log lines as plain text (useful for `curl`)

---

## How it works

1. Fetch all albums via `GET /api/albums`
2. For each album, fetch assets via `GET /api/albums/{id}`
3. Create `OUTPUT_DIR/<album-name>/` and symlink each `asset.originalPath` into it
4. On subsequent runs, skip existing correct symlinks, update wrong ones, remove stale ones
5. Remove album directories that no longer exist in Immich (only if they contain only symlinks)

The sync is **idempotent** — running it multiple times produces the same result.

---

## Troubleshooting

**Symlinks are broken / Plex shows no photos**
- Check that the Immich library is mounted at the exact path returned by `asset.originalPath`.
- Run `docker exec immich-to-plex ls -la /output/MyAlbum/` — if symlink targets show a path that doesn't exist inside the container, the volume mount path is wrong.

**No albums showing up**
- Verify the API key is correct: `curl -H "x-api-key: YOUR_KEY" http://IMMICH_URL/api/albums`
- Check the logs at `http://<host>:8585/logs`

**Plex doesn't see new albums after sync**
- Trigger a Plex library scan manually: Libraries → ⋮ → Scan Library Files
- Or enable automatic scan detection in Plex (requires inotify on the mount)

**Container exits immediately**
- `IMMICH_URL`, `IMMICH_API_KEY`, and `OUTPUT_DIR` are all required — the container will crash if any is missing.

---

## Building from source

```bash
docker build -t immich-to-plex .
```

Pre-built multi-arch images (amd64 + arm64) are published to GHCR on every push to `main`:

```bash
docker pull ghcr.io/andreab-1/immich-plex-sync:latest
```
