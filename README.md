# Search Not Foundarr

Tiny script to replace Huntarr-style periodic searches for Arr apps.

It currently supports:
- Radarr
- Sonarr

It fetches `wanted/missing` and/or `wanted/cutoff`, picks a pool by weight, then picks one random item from that pool and triggers a search command.

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
- `--type` or `$ARR_TYPE` (`radarr` or `sonarr`)
- `--hostname` or `$ARR_HOSTNAME` (with or without `http://` / `https://`)
- `--api-key` or `$ARR_API_KEY`

Optional:
- `--missing-weight` (default `50`)
- `--cutoff-unmet-weight` (default `50`)
- `-v`/`--verbose` increases log level one step each time (`INFO` -> `DEBUG`)
- `-q`/`--quiet` decreases log level one step each time (`INFO` -> `WARNING` -> `ERROR` -> `CRITICAL`)
- `$ARR_PAGE_SIZE` (default `250`)

Weight examples:
- Default behavior (no weight flags): 50/50 missing vs cutoff-unmet.
- Only missing: `--cutoff-unmet-weight 0`
- Only cutoff-unmet: `--missing-weight 0`
- 1/3 missing, 2/3 cutoff-unmet: `--missing-weight 25 --cutoff-unmet-weight 50`

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

More logging:

```bash
uv run search_not_found_arr.py --type sonarr --hostname sonarr.local:8989 -v
```

Less logging:

```bash
uv run search_not_found_arr.py --type sonarr --hostname sonarr.local:8989 -q
```

## Cron Usage

Use a small wrapper script so cron config can stay simple and your API key is not on the command line.

Create secure env file (`chmod 600`):

```bash
mkdir -p ~/.config/search-not-foundarr
cat > ~/.config/search-not-foundarr/env <<'EOF'
export ARR_API_KEY='your_api_key_here'
EOF
chmod 600 ~/.config/search-not-foundarr/env
```

Create wrapper (`chmod 700`):

```bash
mkdir -p ~/.local/bin
cat > ~/.local/bin/search-not-foundarr-sonarr.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
source "$HOME/.config/search-not-foundarr/env"
cd /Users/mikemi/src/Foundarr
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
*/5 * * * * /Users/mikemi/.local/bin/search-not-foundarr-sonarr.sh >> /Users/mikemi/.local/state/search-not-foundarr-sonarr.log 2>&1
```

Repeat with a second wrapper/cron line for Radarr if needed.

## Security Best Practices

- Keep API key in environment, not CLI args:
  - CLI args are often visible in process listings and shell history.
  - Env vars are still sensitive; store/export them from a file with restricted permissions.
- Restrict permissions on secrets files:
  - `chmod 600 ~/.config/search-not-foundarr/env`
- Restrict execution scripts:
  - `chmod 700 ~/.local/bin/search-not-foundarr-*.sh`
