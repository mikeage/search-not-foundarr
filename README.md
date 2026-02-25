# Search Not Foundarr

Tiny script to replace Huntarr-style periodic searches for Arr apps.

Supported apps:
- Radarr
- Sonarr
- Lidarr

Behavior:
- Fetches `wanted/missing` and/or `wanted/cutoff`
- Chooses a pool by weight
- Chooses one random item from that pool
- Triggers one search command
- Prevents searching the same content again within a cooldown window (default 24 hours)

## Requirements

- `uv`
- Python (used by `uv run`)
- Network access to your Arr instance
- Arr API key

## Script

- Script path: `search_not_found_arr.py`
- Run with: `uv run search_not_found_arr.py ...`

## CLI and Environment

Required values (either CLI or env):
- `--type` or `$ARR_TYPE` (`radarr`, `sonarr`, or `lidarr`)
- `--hostname` or `$ARR_HOSTNAME` (with or without `http://` / `https://`)
- `--api-key` or `$ARR_API_KEY`

CLI-only optional:
- `--missing-weight` (default `50`)
- `--cutoff-unmet-weight` (default `50`)
- `-v`/`--verbose` increases log level one step each time (`INFO` -> `DEBUG`)
- `-q`/`--quiet` decreases log level one step each time (`INFO` -> `WARNING` -> `ERROR` -> `CRITICAL`)

Environment-only optional:
- `$ARR_PAGE_SIZE` (default `250`)
- `$ARR_SEARCH_COOLDOWN_HOURS` (default `24`)
- `$ARR_STATE_FILE` (default: `$XDG_STATE_HOME/search-not-foundarr/state.json`, or `~/.local/state/search-not-foundarr/state.json`)

Weight examples:
- Default behavior (no weight flags): 50/50 missing vs cutoff-unmet
- Only missing: `--cutoff-unmet-weight 0`
- Only cutoff-unmet: `--missing-weight 0`
- 1/3 missing and 2/3 cutoff-unmet: `--missing-weight 25 --cutoff-unmet-weight 50`

## Cooldown and State

- The script stores the last search time for each content key in a state file.
- Before rolling, it filters out items searched within the cooldown window.
- If no eligible items remain after filtering, it exits with a warning and does not trigger a command.
- State keys are scoped by app type and host to avoid collisions across different Arr instances.

## Manual Usage

Set API key once in your shell:

```bash
export ARR_API_KEY='your_api_key_here'
```

Run Sonarr:

```bash
uv run search_not_found_arr.py --type sonarr --hostname sonarr.local:8989
```

Run Radarr:

```bash
uv run search_not_found_arr.py --type radarr --hostname https://radarr.example.com
```

Run Lidarr:

```bash
uv run search_not_found_arr.py --type lidarr --hostname lidarr.local:8686
```

Set a custom cooldown (environment only):

```bash
ARR_SEARCH_COOLDOWN_HOURS=12 uv run search_not_found_arr.py --type sonarr --hostname sonarr.local:8989
```

More logging:

```bash
uv run search_not_found_arr.py --type sonarr --hostname sonarr.local:8989 -v
```

Less logging:

```bash
uv run search_not_found_arr.py --type sonarr --hostname sonarr.local:8989 -q
```

## Cron Usage

Create wrapper (`chmod 700`) (this is optional, but make it easier to keep your API_KEY hidden, by not exposing it on the command line):

```bash
mkdir -p ~/.local/bin
cat > ~/.local/bin/search-not-foundarr-sonarr.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
export ARR_API_KEY='your_api_key_here'
export ARR_SEARCH_COOLDOWN_HOURS=24
cd /home/USERNAME/src/Foundarr
exec uv run ./search_not_found_arr.py \
  --type sonarr \
  --hostname sonarr.local:8989 \
  --missing-weight 50 \
  --cutoff-unmet-weight 50 \
  -q
EOF
chmod 700 ~/.local/bin/search-not-foundarr-sonarr.sh
```

Add cron entry (every 5 minutes):

```cron
*/5 * * * * /home/USERNAME/.local/bin/search-not-foundarr-sonarr.sh >> /home/USERNAME/.local/state/search-not-foundarr-sonarr.log 2>&1
```

Repeat with additional wrappers/cron lines for Radarr and/or Lidarr if needed.

## Docker + Cron

This project includes a Docker setup that runs cron in the container and schedules one job per configured server.

Build:

```bash
docker build -t search-not-foundarr:latest .
```

Per-server environment variables:
- `SERVER_<n>_TYPE` (`radarr`, `sonarr`, `lidarr`)
- `SERVER_<n>_HOSTNAME`
- `SERVER_<n>_API_KEY`
- `SERVER_<n>_SCHEDULE` (optional; cron expression, e.g. `*/5 * * * *`)
- `SERVER_<n>_MISSING_WEIGHT` (optional)
- `SERVER_<n>_CUTOFF_UNMET_WEIGHT` (optional)

Global environment variables:
- `ARR_SEARCH_COOLDOWN_HOURS` (default `24`)
- `ARR_PAGE_SIZE` (default `250`)
- `ARR_STATE_FILE` (optional; default inside container user state path)
- `ARR_DEFAULT_SCHEDULE` (default `*/5 * * * *`; used when `SERVER_<n>_SCHEDULE` is not set)

Minimal `docker run` example (single server with only type/hostname/key):

```bash
docker run -d \
  --name search-not-foundarr \
  -e SERVER_1_TYPE=sonarr \
  -e SERVER_1_HOSTNAME=http://sonarr:8989 \
  -e SERVER_1_API_KEY=sonarr_api_key \
  search-not-foundarr:latest
```

`docker-compose` example (multi-server, custom schedules, weights, persistent state):

```yaml
services:
  search-not-foundarr:
    image: search-not-foundarr:latest
    container_name: search-not-foundarr
    restart: unless-stopped
    environment:
      ARR_SEARCH_COOLDOWN_HOURS: "24"
      ARR_PAGE_SIZE: "250"
      ARR_DEFAULT_SCHEDULE: "*/15 * * * *"
      ARR_STATE_FILE: /state/state.json

      SERVER_1_TYPE: sonarr
      SERVER_1_HOSTNAME: http://sonarr:8989
      SERVER_1_API_KEY: ${SONARR_API_KEY}
      SERVER_1_SCHEDULE: "*/5 * * * *"
      SERVER_1_MISSING_WEIGHT: "50"
      SERVER_1_CUTOFF_UNMET_WEIGHT: "50"

      SERVER_2_TYPE: radarr
      SERVER_2_HOSTNAME: http://radarr:7878
      SERVER_2_API_KEY: ${RADARR_API_KEY}
      SERVER_2_SCHEDULE: "*/7 * * * *"
      SERVER_2_MISSING_WEIGHT: "20"
      SERVER_2_CUTOFF_UNMET_WEIGHT: "80"

      SERVER_3_TYPE: lidarr
      SERVER_3_HOSTNAME: http://lidarr:8686
      SERVER_3_API_KEY: ${LIDARR_API_KEY}
      SERVER_3_SCHEDULE: "*/10 * * * *"
    volumes:
      - ./state:/state
```

Run it:

```bash
docker compose up -d
```

Container logs:
- Job output is redirected to container stdout/stderr.
- View with:

```bash
docker logs -f search-not-foundarr
```

## Security Best Practices

- Keep API keys in environment variables, not CLI args.
- Keep wrapper scripts restrictive (`chmod 700`).
- Prefer HTTPS for remote Arr endpoints.
- Keep the state file and logs in user-owned paths with restrictive permissions.
