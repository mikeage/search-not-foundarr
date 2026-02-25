#!/usr/bin/env -S uv run
# /// script
# dependencies = ["requests>=2.32.0"]
# ///
"""Trigger a random search for missing/cutoff-unmet items in Radarr or Sonarr."""

import argparse
import logging
import os
import random
import sys
from typing import Any, NoReturn

import requests

LOGGER = logging.getLogger(__name__)


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
    parser.add_argument("--type", dest="arr_type", help="radarr or sonarr")
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


def choose_records_by_weight(
    missing_records: list[dict[str, Any]],
    cutoff_records: list[dict[str, Any]],
    missing_weight: float,
    cutoff_weight: float,
) -> tuple[list[dict[str, Any]], str]:
    """Choose missing or cutoff pool by weight, then return that pool."""
    if missing_records and cutoff_records:
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
            (missing_records, "missing")
            if chosen_pool == "missing"
            else (cutoff_records, "cutoff-unmet")
        )
    if missing_records:
        LOGGER.debug("Only missing has candidates; using missing pool")
        return missing_records, "missing"
    if cutoff_records:
        LOGGER.debug("Only cutoff-unmet has candidates; using cutoff-unmet pool")
        return cutoff_records, "cutoff-unmet"
    return [], "none"


def fetch_candidate_records(
    session: requests.Session,
    api_base: str,
    page_size: int,
    missing_weight: float,
    cutoff_weight: float,
) -> tuple[list[dict[str, Any]], str]:
    """Fetch weighted pools and return the selected records list."""
    missing_records: list[dict[str, Any]] = []
    cutoff_records: list[dict[str, Any]] = []
    extra_params = {"includeSeries": "true"}

    if missing_weight > 0:
        missing_records = fetch_paged_records(
            session, api_base, "wanted/missing", page_size, extra_params=extra_params
        )
    if cutoff_weight > 0:
        cutoff_records = fetch_paged_records(
            session, api_base, "wanted/cutoff", page_size, extra_params=extra_params
        )

    LOGGER.debug(
        "Fetched candidates: missing=%d cutoff_unmet=%d",
        len(missing_records),
        len(cutoff_records),
    )
    return choose_records_by_weight(
        missing_records,
        cutoff_records,
        missing_weight,
        cutoff_weight,
    )


def summarize_record(
    arr_type: str, record: dict[str, Any], command: dict[str, Any]
) -> str:
    """Return a compact human-readable description of the selected item."""
    if arr_type == "radarr":
        title = (
            record.get("title")
            or (record.get("movie") or {}).get("title")
            or "<unknown>"
        )
        movie_id = (
            as_int(record.get("id"))
            or as_int(record.get("movieId"))
            or as_int((record.get("movie") or {}).get("id"))
        )
        return f"title={title!r} movie_id={movie_id}"

    series_id = as_int(record.get("seriesId") or (record.get("series") or {}).get("id"))
    series_title = (record.get("series") or {}).get("title")
    if series_title:
        series = series_title
    elif series_id is not None:
        series = f"<id:{series_id}>"
    else:
        series = "<unknown>"
    season = as_int(record.get("seasonNumber"))
    episode = as_int(record.get("episodeNumber"))
    episode_title = record.get("title") or "<unknown>"
    if command["name"] == "SeasonSearch":
        return f"series={series!r} season={season}"
    if command["name"] == "SeriesSearch":
        return f"series={series!r}"
    return (
        f"series={series!r} season={season} episode={episode} title={episode_title!r}"
    )


def pick_command(
    arr_type: str, records: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, str]:
    """Build one random search command payload for Radarr or Sonarr."""
    if arr_type == "radarr":
        candidates = []
        for record in records:
            movie_id = (
                as_int(record.get("id"))
                or as_int(record.get("movieId"))
                or as_int((record.get("movie") or {}).get("id"))
            )
            if movie_id is not None:
                candidates.append((record, movie_id))

        LOGGER.debug("Radarr valid candidates in chosen pool: %d", len(candidates))
        if not candidates:
            return None, ""

        record, movie_id = random.choice(candidates)
        command = {"name": "MoviesSearch", "movieIds": [movie_id]}
        return command, summarize_record(arr_type, record, command)

    candidates = []
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
            candidates.append((record, command))
        elif series_id is not None:
            command = {
                "name": "SeriesSearch",
                "seriesId": series_id,
            }
            candidates.append((record, command))
        elif episode_id is not None:
            command = {
                "name": "EpisodeSearch",
                "episodeIds": [episode_id],
            }
            candidates.append((record, command))

    LOGGER.debug("Sonarr valid candidates in chosen pool: %d", len(candidates))
    if not candidates:
        return None, ""

    record, command = random.choice(candidates)
    return command, summarize_record(arr_type, record, command)


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
    if arr_type not in {"radarr", "sonarr"}:
        die("ARR_TYPE must be 'radarr' or 'sonarr'")

    api_base = f"{normalize_host(arg_or_env(args.hostname, 'ARR_HOSTNAME', '--hostname'))}/api/v3"
    missing_weight, cutoff_weight = resolve_weights(args)
    page_size = int(os.getenv("ARR_PAGE_SIZE", "250"))
    LOGGER.debug(
        "Config arr_type=%s api_base=%s page_size=%d missing_weight=%s cutoff_weight=%s",
        arr_type,
        api_base,
        page_size,
        missing_weight,
        cutoff_weight,
    )

    session = requests.Session()
    session.headers.update(
        {
            "X-Api-Key": arg_or_env(args.api_key, "ARR_API_KEY", "--api-key"),
            "Accept": "application/json",
        }
    )
    try:
        records, source_type = fetch_candidate_records(
            session,
            api_base,
            page_size,
            missing_weight,
            cutoff_weight,
        )
        command, item_summary = pick_command(
            arr_type,
            records,
        )
    except requests.HTTPError as error:
        status = error.response.status_code if error.response is not None else "unknown"
        die(f"API request failed ({status}): {error}")
    except requests.RequestException as error:
        die(f"API request failed: {error}")

    if not command:
        LOGGER.info("No monitored missing/cutoff-unmet entries found.")
        return 0

    LOGGER.info(
        "Selected %s item for %s: %s",
        source_type,
        command["name"],
        item_summary,
    )
    LOGGER.debug("Command payload: %s", command)

    try:
        response = session.post(f"{api_base}/command", json=command, timeout=30)
        response.raise_for_status()
        result = response.json()
    except requests.HTTPError as error:
        status = error.response.status_code if error.response is not None else "unknown"
        die(f"Command failed ({status}): {error}")
    except requests.RequestException as error:
        die(f"Command failed: {error}")

    LOGGER.info(
        "Triggered %s (command id=%s, status=%s)",
        command["name"],
        result.get("id"),
        result.get("status"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
