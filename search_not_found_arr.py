#!/usr/bin/env -S uv run
# /// script
# dependencies = ["requests>=2.32.0"]
# ///
"""Trigger a random search for missing/cutoff-unmet items in Arr apps."""

import argparse
import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

import requests

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SelectionSettings:
    """Parameters controlling candidate fetch/filter/select behavior."""

    arr_type: str
    api_base: str
    page_size: int
    scope_key: str
    missing_weight: float
    cutoff_weight: float
    cooldown_seconds: float


def die(message: str) -> NoReturn:
    """Print an error message and terminate with a non-zero exit code."""
    if logging.getLogger().handlers:
        LOGGER.error(message)
    else:
        print(message, file=sys.stderr)
    raise SystemExit(1)


def arg_or_env(value: str | None, env_name: str, option_name: str) -> str:
    """Return CLI value first, then env value, or terminate if neither exists."""
    if value and value.strip():
        return value.strip()
    env_value = os.getenv(env_name, "").strip()
    if env_value:
        return env_value
    die(f"Missing required value: {option_name} (or {env_name})")


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(
        description="Pick one random missing/cutoff-unmet item and trigger an Arr search."
    )
    parser.add_argument("--type", dest="arr_type", help="radarr, sonarr, or lidarr")
    parser.add_argument(
        "--hostname",
        dest="hostname",
        help="Arr hostname, with or without http/https",
    )
    parser.add_argument("--api-key", dest="api_key", help="Arr API key")
    parser.add_argument(
        "--missing-weight",
        dest="missing_weight",
        type=float,
        help="Relative weight for missing items (default: 50)",
    )
    parser.add_argument(
        "--cutoff-unmet-weight",
        dest="cutoff_unmet_weight",
        type=float,
        help="Relative weight for cutoff-unmet items (default: 50)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase log verbosity by one step per use (INFO -> DEBUG)",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="count",
        default=0,
        help="Decrease log verbosity by one step per use (INFO -> WARNING -> ERROR -> CRITICAL)",
    )
    return parser.parse_args()


def normalize_host(hostname: str) -> str:
    """Normalize hostname input to a URL-like value with a scheme."""
    host = hostname.strip().rstrip("/")
    if not host:
        die("ARR_HOSTNAME is empty")
    if "://" not in host:
        host = f"http://{host}"
    return host


def as_int(value: Any) -> int | None:
    """Convert a value to int when possible; otherwise return None."""
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def as_float(value: Any) -> float | None:
    """Convert a value to float when possible; otherwise return None."""
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_cooldown_seconds() -> float:
    """Return cooldown duration in seconds from ARR_SEARCH_COOLDOWN_HOURS."""
    raw_value = os.getenv("ARR_SEARCH_COOLDOWN_HOURS", "24").strip()
    value = as_float(raw_value)
    if value is None:
        die("ARR_SEARCH_COOLDOWN_HOURS must be a number")
    if value < 0:
        die("ARR_SEARCH_COOLDOWN_HOURS must be non-negative")
    return value * 3600


def resolve_state_path() -> Path:
    """Return state file path, defaulting to XDG-compatible user state directory."""
    raw_path = os.getenv("ARR_STATE_FILE", "").strip()
    if raw_path:
        return Path(raw_path).expanduser()

    state_root = Path(
        os.getenv("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
    )
    return state_root / "search-not-foundarr" / "state.json"


def load_state(state_path: Path) -> dict[str, float]:
    """Load persisted last-search timestamps from disk."""
    if not state_path.exists():
        return {}

    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        LOGGER.warning("State file unreadable (%s): %s", state_path, error)
        return {}

    raw_items: Any
    if isinstance(payload, dict) and isinstance(payload.get("last_searched"), dict):
        raw_items = payload.get("last_searched")
    elif isinstance(payload, dict):
        raw_items = payload
    else:
        LOGGER.warning("Unexpected state file format in %s; ignoring", state_path)
        return {}

    state: dict[str, float] = {}
    for key, value in raw_items.items():
        timestamp = as_float(value)
        if isinstance(key, str) and timestamp is not None:
            state[key] = timestamp

    return state


def prune_state(
    state: dict[str, float], now_ts: float, cooldown_seconds: float
) -> None:
    """Drop entries that are older than the cooldown window."""
    if cooldown_seconds <= 0:
        return

    for key in [k for k, ts in state.items() if now_ts - ts >= cooldown_seconds]:
        del state[key]


def save_state(state_path: Path, state: dict[str, float]) -> None:
    """Persist last-search timestamps to disk."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "last_searched": state}
    temp_path = state_path.with_suffix(f"{state_path.suffix}.tmp")
    temp_path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(state_path)


def fetch_paged_records(
    session: requests.Session,
    api_base: str,
    path: str,
    page_size: int,
    extra_params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Fetch all pages from a paged Arr endpoint and return the records list."""
    page = 1
    records: list[dict[str, Any]] = []

    while True:
        params: dict[str, Any] = {"page": page, "pageSize": page_size}
        if extra_params:
            params.update(extra_params)

        LOGGER.debug("Fetching %s params=%s", path, params)
        response = session.get(
            f"{api_base}/{path}",
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()

        page_records = payload.get("records") or []
        if not isinstance(page_records, list):  # Defensive: unexpected API shape.
            die(f"Unexpected response from {path}: records is not a list")

        records.extend(page_records)
        total_records = as_int(payload.get("totalRecords"))

        if not page_records:
            break
        if total_records is not None and len(records) >= total_records:
            break
        if total_records is None and len(page_records) < page_size:
            break

        page += 1

    LOGGER.debug("Fetched %d total records from %s", len(records), path)
    return records


def resolve_weights(args: argparse.Namespace) -> tuple[float, float]:
    """Resolve/validate missing and cutoff-unmet weights from CLI values."""
    missing_weight = 50.0 if args.missing_weight is None else float(args.missing_weight)
    cutoff_weight = (
        50.0 if args.cutoff_unmet_weight is None else float(args.cutoff_unmet_weight)
    )

    if missing_weight < 0 or cutoff_weight < 0:
        die("Weights must be non-negative")
    if missing_weight == 0 and cutoff_weight == 0:
        die("At least one weight must be greater than zero")

    return missing_weight, cutoff_weight


def resolve_api_version(arr_type: str) -> str:
    """Return API version segment for the selected Arr type."""
    if arr_type == "lidarr":
        return "v1"
    return "v3"


def build_selection_settings(
    args: argparse.Namespace, arr_type: str
) -> SelectionSettings:
    """Build selection settings from args and environment values."""
    api_version = resolve_api_version(arr_type)
    api_base = (
        f"{normalize_host(arg_or_env(args.hostname, 'ARR_HOSTNAME', '--hostname'))}"
        f"/api/{api_version}"
    )
    missing_weight, cutoff_weight = resolve_weights(args)
    page_size = int(os.getenv("ARR_PAGE_SIZE", "250"))
    cooldown_seconds = resolve_cooldown_seconds()
    return SelectionSettings(
        arr_type=arr_type,
        api_base=api_base,
        page_size=page_size,
        scope_key=f"{arr_type}:{api_base}",
        missing_weight=missing_weight,
        cutoff_weight=cutoff_weight,
        cooldown_seconds=cooldown_seconds,
    )


def choose_pool_by_weight(
    missing_items: list[dict[str, Any]],
    cutoff_items: list[dict[str, Any]],
    missing_weight: float,
    cutoff_weight: float,
) -> tuple[list[dict[str, Any]], str]:
    """Choose missing or cutoff pool by weight, then return that pool."""
    if missing_items and cutoff_items:
        missing_probability = missing_weight / (missing_weight + cutoff_weight)
        roll = random.random()
        chosen_pool = "missing" if roll < missing_probability else "cutoff-unmet"
        LOGGER.debug(
            "Pool choice roll=%.5f missing_probability=%.5f chosen=%s",
            roll,
            missing_probability,
            chosen_pool,
        )
        return (
            (missing_items, "missing")
            if chosen_pool == "missing"
            else (cutoff_items, "cutoff-unmet")
        )
    if missing_items:
        LOGGER.debug("Only missing has candidates; using missing pool")
        return missing_items, "missing"
    if cutoff_items:
        LOGGER.debug("Only cutoff-unmet has candidates; using cutoff-unmet pool")
        return cutoff_items, "cutoff-unmet"
    return [], "none"


def fetch_extra_params(arr_type: str) -> dict[str, str] | None:
    """Return extra query params needed for wanted endpoints per Arr type."""
    if arr_type == "sonarr":
        return {"includeSeries": "true"}
    if arr_type == "lidarr":
        return {"includeArtist": "true"}
    return None


def fetch_wanted_records(
    settings: SelectionSettings,
    session: requests.Session,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch missing and cutoff records (respecting zero-weight skips)."""
    missing_records: list[dict[str, Any]] = []
    cutoff_records: list[dict[str, Any]] = []
    extra_params = fetch_extra_params(settings.arr_type)

    if settings.missing_weight > 0:
        missing_records = fetch_paged_records(
            session,
            settings.api_base,
            "wanted/missing",
            settings.page_size,
            extra_params=extra_params,
        )
    if settings.cutoff_weight > 0:
        cutoff_records = fetch_paged_records(
            session,
            settings.api_base,
            "wanted/cutoff",
            settings.page_size,
            extra_params=extra_params,
        )

    LOGGER.debug(
        "Fetched candidates: missing=%d cutoff_unmet=%d",
        len(missing_records),
        len(cutoff_records),
    )
    return missing_records, cutoff_records


def summarize_radarr_record(record: dict[str, Any]) -> str:
    """Summarize a Radarr record for logs."""
    title = (
        record.get("title") or (record.get("movie") or {}).get("title") or "<unknown>"
    )
    movie_id = (
        as_int(record.get("id"))
        or as_int(record.get("movieId"))
        or as_int((record.get("movie") or {}).get("id"))
    )
    return f"title={title!r} movie_id={movie_id}"


def summarize_lidarr_record(record: dict[str, Any], command: dict[str, Any]) -> str:
    """Summarize a Lidarr record for logs."""
    artist = record.get("artist") or {}
    artist_id = as_int(record.get("artistId") or artist.get("id"))
    artist_name = (
        artist.get("artistName")
        or artist.get("name")
        or (f"<id:{artist_id}>" if artist_id is not None else "<unknown>")
    )
    if command["name"] == "ArtistSearch":
        return f"artist={artist_name!r} artist_id={artist_id}"

    album_id = as_int(record.get("id") or record.get("albumId"))
    album_title = record.get("title") or "<unknown>"
    return f"artist={artist_name!r} album={album_title!r} album_id={album_id}"


def summarize_sonarr_record(record: dict[str, Any], command: dict[str, Any]) -> str:
    """Summarize a Sonarr record for logs."""
    series_id = as_int(record.get("seriesId") or (record.get("series") or {}).get("id"))
    series_title = (record.get("series") or {}).get("title")
    if series_title:
        series = series_title
    elif series_id is not None:
        series = f"<id:{series_id}>"
    else:
        series = "<unknown>"

    season = as_int(record.get("seasonNumber"))
    if command["name"] == "SeasonSearch":
        return f"series={series!r} season={season}"
    if command["name"] == "SeriesSearch":
        return f"series={series!r}"

    episode = as_int(record.get("episodeNumber"))
    episode_title = record.get("title") or "<unknown>"
    return (
        f"series={series!r} season={season} episode={episode} title={episode_title!r}"
    )


def summarize_record(
    arr_type: str, record: dict[str, Any], command: dict[str, Any]
) -> str:
    """Return a compact human-readable description of the selected item."""
    if arr_type == "radarr":
        return summarize_radarr_record(record)
    if arr_type == "lidarr":
        return summarize_lidarr_record(record, command)
    return summarize_sonarr_record(record, command)


def build_radarr_candidates(
    source_type: str, scope_key: str, records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build Radarr candidate entries."""
    candidates: list[dict[str, Any]] = []
    for record in records:
        movie_id = (
            as_int(record.get("id"))
            or as_int(record.get("movieId"))
            or as_int((record.get("movie") or {}).get("id"))
        )
        if movie_id is None:
            continue

        command = {"name": "MoviesSearch", "movieIds": [movie_id]}
        candidates.append(
            {
                "key": f"{scope_key}:movie:{movie_id}",
                "source_type": source_type,
                "command": command,
                "summary": summarize_record("radarr", record, command),
            }
        )

    LOGGER.debug("Radarr valid candidates in %s pool: %d", source_type, len(candidates))
    return candidates


def build_lidarr_candidates(
    source_type: str, scope_key: str, records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build Lidarr candidate entries."""
    candidates: list[dict[str, Any]] = []
    for record in records:
        album_id = as_int(record.get("id") or record.get("albumId"))
        artist_id = as_int(
            record.get("artistId") or (record.get("artist") or {}).get("id")
        )

        if album_id is not None:
            command = {"name": "AlbumSearch", "albumIds": [album_id]}
            content_key = f"{scope_key}:album:{album_id}"
        elif artist_id is not None:
            command = {"name": "ArtistSearch", "artistId": artist_id}
            content_key = f"{scope_key}:artist:{artist_id}"
        else:
            continue

        candidates.append(
            {
                "key": content_key,
                "source_type": source_type,
                "command": command,
                "summary": summarize_record("lidarr", record, command),
            }
        )

    LOGGER.debug("Lidarr valid candidates in %s pool: %d", source_type, len(candidates))
    return candidates


def build_sonarr_candidates(
    source_type: str, scope_key: str, records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build Sonarr candidate entries."""
    candidates: list[dict[str, Any]] = []
    for record in records:
        episode_id = as_int(record.get("id") or record.get("episodeId"))
        series_id = as_int(
            record.get("seriesId") or (record.get("series") or {}).get("id")
        )
        season_number = as_int(record.get("seasonNumber"))

        if series_id is not None and season_number is not None:
            command = {
                "name": "SeasonSearch",
                "seriesId": series_id,
                "seasonNumber": season_number,
            }
            content_key = f"{scope_key}:series:{series_id}:season:{season_number}"
        elif series_id is not None:
            command = {
                "name": "SeriesSearch",
                "seriesId": series_id,
            }
            content_key = f"{scope_key}:series:{series_id}"
        elif episode_id is not None:
            command = {
                "name": "EpisodeSearch",
                "episodeIds": [episode_id],
            }
            content_key = f"{scope_key}:episode:{episode_id}"
        else:
            continue

        candidates.append(
            {
                "key": content_key,
                "source_type": source_type,
                "command": command,
                "summary": summarize_record("sonarr", record, command),
            }
        )

    LOGGER.debug("Sonarr valid candidates in %s pool: %d", source_type, len(candidates))
    return candidates


def build_candidates(
    arr_type: str,
    source_type: str,
    scope_key: str,
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build candidate command entries with stable content keys."""
    if arr_type == "radarr":
        return build_radarr_candidates(source_type, scope_key, records)
    if arr_type == "lidarr":
        return build_lidarr_candidates(source_type, scope_key, records)
    return build_sonarr_candidates(source_type, scope_key, records)


def filter_candidates_by_cooldown(
    candidates: list[dict[str, Any]],
    last_searched: dict[str, float],
    now_ts: float,
    cooldown_seconds: float,
) -> list[dict[str, Any]]:
    """Filter out candidates that were searched within the cooldown window."""
    if cooldown_seconds <= 0:
        return candidates

    filtered: list[dict[str, Any]] = []
    blocked_count = 0

    for candidate in candidates:
        last_ts = last_searched.get(candidate["key"])
        if last_ts is None:
            filtered.append(candidate)
            continue

        elapsed = now_ts - last_ts
        if elapsed >= cooldown_seconds:
            filtered.append(candidate)
            continue

        blocked_count += 1
        LOGGER.debug(
            "Cooldown skip key=%s remaining=%.0fs",
            candidate["key"],
            cooldown_seconds - elapsed,
        )

    LOGGER.debug(
        "Eligible candidates after cooldown: %d/%d (blocked=%d)",
        len(filtered),
        len(candidates),
        blocked_count,
    )
    return filtered


def pick_candidate(
    missing_candidates: list[dict[str, Any]],
    cutoff_candidates: list[dict[str, Any]],
    missing_weight: float,
    cutoff_weight: float,
) -> dict[str, Any] | None:
    """Pick one random candidate using weighted pool selection."""
    pool, _source_type = choose_pool_by_weight(
        missing_candidates,
        cutoff_candidates,
        missing_weight,
        cutoff_weight,
    )
    return random.choice(pool) if pool else None


def create_session(api_key: str) -> requests.Session:
    """Create a configured requests session for Arr API calls."""
    session = requests.Session()
    session.headers.update(
        {
            "X-Api-Key": api_key,
            "Accept": "application/json",
        }
    )
    return session


def select_candidate(
    settings: SelectionSettings,
    session: requests.Session,
    last_searched: dict[str, float],
) -> dict[str, Any] | None:
    """Fetch, build, cooldown-filter, and pick one weighted candidate."""
    missing_records, cutoff_records = fetch_wanted_records(settings, session)
    missing_candidates = build_candidates(
        settings.arr_type,
        "missing",
        settings.scope_key,
        missing_records,
    )
    cutoff_candidates = build_candidates(
        settings.arr_type,
        "cutoff-unmet",
        settings.scope_key,
        cutoff_records,
    )

    now_ts = time.time()
    missing_candidates = filter_candidates_by_cooldown(
        missing_candidates,
        last_searched,
        now_ts,
        settings.cooldown_seconds,
    )
    cutoff_candidates = filter_candidates_by_cooldown(
        cutoff_candidates,
        last_searched,
        now_ts,
        settings.cooldown_seconds,
    )
    return pick_candidate(
        missing_candidates,
        cutoff_candidates,
        settings.missing_weight,
        settings.cutoff_weight,
    )


def select_candidate_or_die(
    settings: SelectionSettings,
    session: requests.Session,
    last_searched: dict[str, float],
) -> dict[str, Any] | None:
    """Run candidate selection and handle request errors consistently."""
    try:
        return select_candidate(
            settings,
            session,
            last_searched,
        )
    except requests.HTTPError as error:
        status = error.response.status_code if error.response is not None else "unknown"
        die(f"API request failed ({status}): {error}")
    except requests.RequestException as error:
        die(f"API request failed: {error}")


def execute_command_or_die(
    session: requests.Session, api_base: str, command: dict[str, Any]
) -> dict[str, Any]:
    """Execute Arr command and handle request errors consistently."""
    try:
        response = session.post(f"{api_base}/command", json=command, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.HTTPError as error:
        status = error.response.status_code if error.response is not None else "unknown"
        die(f"Command failed ({status}): {error}")
    except requests.RequestException as error:
        die(f"Command failed: {error}")


def persist_state_entry(
    state: dict[str, float],
    candidate_key: str,
    state_path: Path,
    cooldown_seconds: float,
) -> None:
    """Record the selected candidate timestamp and save state file."""
    completed_ts = time.time()
    state[candidate_key] = completed_ts
    prune_state(state, completed_ts, cooldown_seconds)
    try:
        save_state(state_path, state)
    except OSError as error:
        LOGGER.warning("Failed to write state file %s: %s", state_path, error)


def resolve_log_level(verbose_count: int, quiet_count: int) -> int:
    """Resolve logging level from -v/-q counters, defaulting to INFO."""
    levels = [
        logging.CRITICAL,
        logging.ERROR,
        logging.WARNING,
        logging.INFO,
        logging.DEBUG,
    ]
    base_index = 3  # INFO
    index = base_index + verbose_count - quiet_count
    index = max(index, 0)
    index = min(index, len(levels) - 1)
    return levels[index]


def configure_logging(level: int) -> None:
    """Initialize logging output format and level."""
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


def main() -> int:
    """Run the command-line workflow."""
    args = parse_args()
    configure_logging(resolve_log_level(args.verbose, args.quiet))
    arr_type = arg_or_env(args.arr_type, "ARR_TYPE", "--type").lower()
    if arr_type not in {"radarr", "sonarr", "lidarr"}:
        die("ARR_TYPE must be 'radarr', 'sonarr', or 'lidarr'")

    settings = build_selection_settings(args, arr_type)
    state_path = resolve_state_path()
    last_searched = load_state(state_path)
    prune_state(last_searched, time.time(), settings.cooldown_seconds)
    LOGGER.debug(
        (
            "Config arr_type=%s api_base=%s page_size=%d missing_weight=%s "
            "cutoff_weight=%s cooldown_seconds=%s state_path=%s state_entries=%d"
        ),
        settings.arr_type,
        settings.api_base,
        settings.page_size,
        settings.missing_weight,
        settings.cutoff_weight,
        settings.cooldown_seconds,
        state_path,
        len(last_searched),
    )

    session = create_session(arg_or_env(args.api_key, "ARR_API_KEY", "--api-key"))
    candidate = select_candidate_or_die(settings, session, last_searched)

    if not candidate:
        LOGGER.warning(
            (
                "No eligible missing/cutoff-unmet entries remain after cooldown "
                "(cooldown_hours=%.2f)."
            ),
            settings.cooldown_seconds / 3600,
        )
        return 0

    command = candidate["command"]

    LOGGER.info(
        "Selected %s item for %s: %s",
        candidate["source_type"],
        command["name"],
        candidate["summary"],
    )
    LOGGER.debug("Command payload: %s", command)

    result = execute_command_or_die(session, settings.api_base, command)
    persist_state_entry(
        last_searched,
        candidate["key"],
        state_path,
        settings.cooldown_seconds,
    )

    LOGGER.info(
        "Triggered %s (command id=%s, status=%s)",
        command["name"],
        result.get("id"),
        result.get("status"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
