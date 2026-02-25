#!/usr/bin/env -S uv run
# /// script
# dependencies = ["requests>=2.32.0"]
# ///
"""Trigger a random search for missing/cutoff-unmet items in Radarr or Sonarr."""

import argparse
import os
import random
import sys
from typing import Any, NoReturn

import requests


def die(message: str) -> NoReturn:
    """Print an error message and terminate with a non-zero exit code."""
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
) -> list[dict[str, Any]]:
    """Fetch all pages from a paged Arr endpoint and return the records list."""
    page = 1
    records: list[dict[str, Any]] = []

    while True:
        response = session.get(
            f"{api_base}/{path}",
            params={"page": page, "pageSize": page_size},
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
) -> list[dict[str, Any]]:
    """Choose missing or cutoff pool by weight, then return that pool."""
    if missing_records and cutoff_records:
        return (
            missing_records
            if random.random() < (missing_weight / (missing_weight + cutoff_weight))
            else cutoff_records
        )
    if missing_records:
        return missing_records
    if cutoff_records:
        return cutoff_records
    return []


def fetch_candidate_records(
    session: requests.Session,
    api_base: str,
    page_size: int,
    missing_weight: float,
    cutoff_weight: float,
) -> list[dict[str, Any]]:
    """Fetch weighted pools and return the selected records list."""
    missing_records: list[dict[str, Any]] = []
    cutoff_records: list[dict[str, Any]] = []

    if missing_weight > 0:
        missing_records = fetch_paged_records(
            session, api_base, "wanted/missing", page_size
        )
    if cutoff_weight > 0:
        cutoff_records = fetch_paged_records(
            session, api_base, "wanted/cutoff", page_size
        )

    return choose_records_by_weight(
        missing_records,
        cutoff_records,
        missing_weight,
        cutoff_weight,
    )


def pick_command(arr_type: str, records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Build one random search command payload for Radarr or Sonarr."""
    if arr_type == "radarr":
        movie_ids = {
            movie_id
            for record in records
            for movie_id in (
                as_int(record.get("id")),
                as_int(record.get("movieId")),
                as_int((record.get("movie") or {}).get("id")),
            )
            if movie_id is not None
        }
        return (
            {"name": "MoviesSearch", "movieIds": [random.choice(list(movie_ids))]}
            if movie_ids
            else None
        )

    commands = {}
    for record in records:
        episode_id = as_int(record.get("id") or record.get("episodeId"))
        series_id = as_int(
            record.get("seriesId") or (record.get("series") or {}).get("id")
        )
        season_number = as_int(record.get("seasonNumber"))

        if series_id is not None and season_number is not None:
            commands[("season", series_id, season_number)] = {
                "name": "SeasonSearch",
                "seriesId": series_id,
                "seasonNumber": season_number,
            }
        elif series_id is not None:
            commands[("series", series_id)] = {
                "name": "SeriesSearch",
                "seriesId": series_id,
            }
        elif episode_id is not None:
            commands[("episode", episode_id)] = {
                "name": "EpisodeSearch",
                "episodeIds": [episode_id],
            }

    return random.choice(list(commands.values())) if commands else None


def main() -> int:
    """Run the command-line workflow."""
    args = parse_args()
    arr_type = arg_or_env(args.arr_type, "ARR_TYPE", "--type").lower()
    if arr_type not in {"radarr", "sonarr"}:
        die("ARR_TYPE must be 'radarr' or 'sonarr'")

    api_base = f"{normalize_host(arg_or_env(args.hostname, 'ARR_HOSTNAME', '--hostname'))}/api/v3"
    missing_weight, cutoff_weight = resolve_weights(args)

    session = requests.Session()
    session.headers.update(
        {
            "X-Api-Key": arg_or_env(args.api_key, "ARR_API_KEY", "--api-key"),
            "Accept": "application/json",
        }
    )
    try:
        command = pick_command(
            arr_type,
            fetch_candidate_records(
                session,
                api_base,
                int(os.getenv("ARR_PAGE_SIZE", "250")),
                missing_weight,
                cutoff_weight,
            ),
        )
    except requests.HTTPError as error:
        status = error.response.status_code if error.response is not None else "unknown"
        die(f"API request failed ({status}): {error}")
    except requests.RequestException as error:
        die(f"API request failed: {error}")

    if not command:
        print("No monitored missing/cutoff-unmet entries found.")
        return 0

    try:
        response = session.post(f"{api_base}/command", json=command, timeout=30)
        response.raise_for_status()
        result = response.json()
    except requests.HTTPError as error:
        status = error.response.status_code if error.response is not None else "unknown"
        die(f"Command failed ({status}): {error}")
    except requests.RequestException as error:
        die(f"Command failed: {error}")

    print(
        "Triggered "
        f"{command['name']} "
        f"(command id={result.get('id')}, status={result.get('status')})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
