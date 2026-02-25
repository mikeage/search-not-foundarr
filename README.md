# Search Not Foundarr

Tiny script to replace Huntarr-style periodic searches for Arr apps.

Supported apps:
- Radarr
- Sonarr
- Lidarr

Behavior:
- Fetches `wanted/missing` and/or `wanted/cutoff`
- Chooses a pool by weight (default 50/50)
- Chooses one random item from that pool
- Triggers one search command
- Prevents searching the same content again within a cooldown window (default 24 hours)

## Requirements

- `uv` (you can run without it, but you have to know how to install `requests`. Trivial, but I've only documented `uv`)
- Network access to your Arr instance
- Arr API key

OR

- docker (includes cron)

## Script

- Run with: `uv run search_not_foundarr.py ...`

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
- `$ARR_PAGE_SIZE` (default `250`; I'm not sure if this is always safe to change)
- `$ARR_SEARCH_COOLDOWN_HOURS` (default `24`)
- `$ARR_STATE_FILE` (default: `$XDG_STATE_HOME/search-not-foundarr/state.json`, or `~/.local/state/search-not-foundarr/state.json`)

Weight examples:
- Default behavior (no weight flags): 50/50 missing vs cutoff-unmet, i.e., half of the searches will be for a missing item, and half for a cutoff-unmet item.
- Only missing: `--cutoff-unmet-weight 0`
- Only cutoff-unmet: `--missing-weight 0`
- 1/3 missing and 2/3 cutoff-unmet: `--missing-weight 25 --cutoff-unmet-weight 50`, or any other combination in the ratio of 1:2.

## Cooldown and State

- The script stores the last search time for each content key in a state file.
- Before picking an item from the pool (see the weight parameters for details), it filters out items searched within the cooldown window.
- If no eligible items remain after filtering, it tries the other pool, unless it's weight is 0. If there is still nothing available, it exits with a warning and does not trigger a search.

## Manual Usage

Set API key once in your shell:

```bash
export ARR_API_KEY='your_api_key_here'
```

Run Sonarr:

```bash
uv run search_not_foundarr.py --type sonarr --hostname sonarr.local:8989
```

Run Radarr:

```bash
uv run search_not_foundarr.py --type radarr --hostname https://radarr.example.com
```

Run Lidarr:

```bash
uv run search_not_foundarr.py --type lidarr --hostname lidarr.lan:8686
```

Set a custom cooldown (environment only). If you really need this regularly, you should probably figure out why your arrs are missing things so often!:

```bash
ARR_SEARCH_COOLDOWN_HOURS=12 uv run search_not_foundarr.py --type sonarr --hostname sonarr.local:8989
```

More logging:

```bash
uv run search_not_foundarr.py --type sonarr --hostname sonarr.local:8989 -v
```

Less logging:

```bash
uv run search_not_foundarr.py --type sonarr --hostname sonarr.local:8989 -q
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
exec uv run ./search_not_foundarr.py \
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

Build locally (optional):

```bash
docker build -t search-not-foundarr:latest .
```

Published image name from GitHub Actions:
- `ghcr.io/mikeage/search-not-foundarr:latest`
- Versioned tags are also published (for example `v1.2.3` whenever I get around to tagging anything and `sha-<commit>`).

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
- `XDG_STATE_HOME` (optional; used only when `ARR_STATE_FILE` is not set)

Minimal `docker run` example, but you're better off using `docker compose`:

```bash
docker run -d \
  --name search-not-foundarr \
  -e SERVER_1_TYPE=sonarr \
  -e SERVER_1_HOSTNAME=http://sonarr:8989 \
  -e SERVER_1_API_KEY=sonarr_api_key \
  ghcr.io/mikeage/search-not-foundarr:latest
```

`docker compose` example (multi-server, custom schedules, weights, persistent state):

```yaml
services:
  cron:
    image: ghcr.io/mikeage/search-not-foundarr:latest
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
      SERVER_2_SCHEDULE: "1-59/5 * * * *"
      SERVER_2_MISSING_WEIGHT: "20"
      SERVER_2_CUTOFF_UNMET_WEIGHT: "80"

      SERVER_3_TYPE: lidarr
      SERVER_3_HOSTNAME: http://lidarr:8686
      SERVER_3_API_KEY: ${LIDARR_API_KEY}
      SERVER_3_SCHEDULE: "2-59/10 * * * *"
    volumes:
      - ./state:/state
```

Run it:

```bash
docker compose up -d
```

## Security Best Practices

- Keep API keys in environment variables, not CLI args.
- Keep wrapper scripts restrictive (`chmod 700`).
