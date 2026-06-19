"""
tmdb_client.py

A small, defensive wrapper around the TMDB API:
  - conservative rate limiting (well under TMDB's free-tier ceiling)
  - automatic retry on transient errors (429, 5xx) with exponential backoff
  - normalizes the handful of fields this pipeline cares about

This is intentionally NOT a general-purpose TMDB SDK. It exposes exactly
what the enrichment pipeline needs: search-by-title and fetch-details.
"""

import os
import time
import requests
from typing import Any, Dict, List, Optional


class TMDBError(Exception):
    pass


class TMDBClient:
    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(self, api_key: Optional[str] = None, min_request_interval: float = 0.06):
        """
        min_request_interval: minimum seconds between requests (default ~16/sec,
        well under TMDB's free-tier ~50/sec ceiling, to leave headroom for retries
        and avoid hammering the API during development/testing).
        """
        self.api_key = api_key or os.getenv("TMDB_API_KEY")
        if not self.api_key:
            raise TMDBError(
                "No TMDB API key found. Set TMDB_API_KEY in your .env file "
                "or pass api_key= explicitly."
            )
        self._min_interval = min_request_interval
        self._last_request_time = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        wait = self._min_interval - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request_time = time.monotonic()

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None, max_retries: int = 4) -> Dict[str, Any]:
        params = dict(params or {})
        params["api_key"] = self.api_key

        for attempt in range(max_retries + 1):
            self._throttle()
            resp = requests.get(f"{self.BASE_URL}{path}", params=params, timeout=15)

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", "1"))
                time.sleep(retry_after + 0.5)
                continue

            if resp.status_code >= 500:
                time.sleep(2 ** attempt)
                continue

            # 4xx other than 429 — not retryable
            raise TMDBError(f"TMDB API error {resp.status_code} on {path}: {resp.text[:300]}")

        raise TMDBError(f"TMDB request failed after {max_retries} retries: {path}")

    def search_movie(self, title: str, year: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Returns a list of candidate matches (raw TMDB search results), ordered
        as TMDB returns them (roughly by relevance/popularity).
        """
        params = {"query": title, "include_adult": "false"}
        if year:
            params["year"] = year
        data = self._get("/search/movie", params=params)
        return data.get("results", []) or []

    def get_movie_details(self, tmdb_id: int) -> Dict[str, Any]:
        """
        Fetches full details for a single movie and normalizes the fields
        this pipeline cares about.
        """
        data = self._get(f"/movie/{tmdb_id}")
        genres = [g.get("name") for g in (data.get("genres") or []) if g.get("name")]
        return {
            "tmdb_id": data.get("id"),
            "tmdb_title": data.get("title"),
            "release_date": data.get("release_date") or None,
            "runtime_minutes": data.get("runtime"),
            "genres": ", ".join(genres) if genres else None,
            "budget": data.get("budget") or None,
            "revenue": data.get("revenue") or None,
            "vote_average": data.get("vote_average"),
            "popularity": data.get("popularity"),
            "original_language": data.get("original_language"),
        }
