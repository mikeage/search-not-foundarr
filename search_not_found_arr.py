#!/usr/bin/env -S uv run
# /// script
# dependencies = ["requests>=2.32.0"]
# ///

import os
import random
import sys

import requests


def die(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        die(f"Missing required environment variable: {name}")
    return value


def normalize_host(hostname: str) -> str:
    host = hostname.strip().rstrip("/")
    if not host:
        die("ARR_HOSTNAME is empty")
    if "://" not in host:
        host = f"http://{host}"
    return host


def as_int(value):
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
):
    page = 1
    records = []

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


def pick_command(arr_type: str, records):
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
        series_id = as_int(record.get("seriesId") or (record.get("series") or {}).get("id"))
        season_number = as_int(record.get("seasonNumber"))

        if series_id is not None and season_number is not None:
            commands[("season", series_id, season_number)] = {
                "name": "SeasonSearch",
                "seriesId": series_id,
                "seasonNumber": season_number,
            }
        elif series_id is not None:
            commands[("series", series_id)] = {"name": "SeriesSearch", "seriesId": series_id}
        elif episode_id is not None:
            commands[("episode", episode_id)] = {"name": "EpisodeSearch", "episodeIds": [episode_id]}

    return random.choice(list(commands.values())) if commands else None


def main() -> int:
    arr_type = env_required("ARR_TYPE").lower()
    if arr_type not in {"radarr", "sonarr"}:
        die("ARR_TYPE must be 'radarr' or 'sonarr'")

    host = normalize_host(env_required("ARR_HOSTNAME"))
    api_key = env_required("ARR_API_KEY")
    api_base = f"{host}/api/v3"
    page_size = int(os.getenv("ARR_PAGE_SIZE", "250"))

    session = requests.Session()
    session.headers.update({"X-Api-Key": api_key, "Accept": "application/json"})

    try:
        records = fetch_paged_records(session, api_base, "wanted/missing", page_size)
        records.extend(fetch_paged_records(session, api_base, "wanted/cutoff", page_size))
    except requests.HTTPError as error:
        status = error.response.status_code if error.response is not None else "unknown"
        die(f"API request failed ({status}): {error}")
    except requests.RequestException as error:
        die(f"API request failed: {error}")

    command = pick_command(arr_type, records)

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
        f"Triggered {command['name']} (command id={result.get('id')}, status={result.get('status')})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
