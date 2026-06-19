"""
pipeline.py

Orchestrates the enrichment run: for every row in the user's source data,
resolve it against TMDB and produce an enriched output row. Source/output
agnostic — callers (CLI or web UI) decide where the data comes from and
where it goes.
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from matcher import find_best_match
from tmdb_client import TMDBClient, TMDBError

logger = logging.getLogger("filmDataEnrichmentPipeline")


def run_enrichment(
    rows: List[Dict[str, Any]],
    title_column: str,
    date_column: Optional[str],
    id_column: Optional[str],
    client: TMDBClient,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> List[Dict[str, Any]]:
    """
    rows: the user's source data as a list of dicts (from CSV or SQLite)
    title_column: which column holds the movie title
    date_column: optional column holding a date/year hint (semantics unknown
                 — see matcher.py docstring for why this is treated as soft)
    id_column: optional column to carry through as a stable identifier
               (e.g. an existing movie_id), so output can be joined back
    progress_callback: optional fn(current_index, total) for UI progress bars

    Returns a list of dicts shaped per data_io.ENRICHMENT_COLUMNS.
    """
    output_rows: List[Dict[str, Any]] = []
    total = len(rows)

    for i, row in enumerate(rows):
        raw_title = (row.get(title_column) or "").strip()
        source_id = row.get(id_column) if id_column else str(i)

        if progress_callback:
            progress_callback(i + 1, total)

        if not raw_title:
            output_rows.append(_empty_result(source_id, raw_title, "skipped_empty_title"))
            continue

        provided_year = None
        if date_column:
            raw_date = row.get(date_column)
            try:
                provided_year = int(str(raw_date)[:4]) if raw_date else None
            except (ValueError, TypeError):
                provided_year = None

        try:
            candidates = client.search_movie(raw_title, year=provided_year)
        except TMDBError as e:
            logger.warning("TMDB search failed for %r: %s", raw_title, e)
            output_rows.append(_empty_result(source_id, raw_title, f"search_error: {e}"))
            continue

        result = find_best_match(raw_title, candidates, provided_year=provided_year)

        if not result.matched:
            output_rows.append({
                "source_id": source_id,
                "source_title": raw_title,
                "match_status": "unmatched",
                "match_confidence": result.confidence,
                "match_reason": result.reason,
                "tmdb_id": "", "tmdb_title": "", "release_date": "",
                "runtime_minutes": "", "genres": "", "budget": "",
                "revenue": "", "vote_average": "", "popularity": "",
                "original_language": "",
            })
            continue

        try:
            details = client.get_movie_details(result.tmdb_id)
        except TMDBError as e:
            logger.warning("TMDB details fetch failed for tmdb_id=%s: %s", result.tmdb_id, e)
            output_rows.append(_empty_result(source_id, raw_title, f"details_fetch_error: {e}"))
            continue

        output_rows.append({
            "source_id": source_id,
            "source_title": raw_title,
            "match_status": "matched",
            "match_confidence": result.confidence,
            "match_reason": result.reason,
            "tmdb_id": details["tmdb_id"],
            "tmdb_title": details["tmdb_title"],
            "release_date": details["release_date"] or "",
            "runtime_minutes": details["runtime_minutes"] or "",
            "genres": details["genres"] or "",
            "budget": details["budget"] or "",
            "revenue": details["revenue"] or "",
            "vote_average": details["vote_average"] or "",
            "popularity": details["popularity"] or "",
            "original_language": details["original_language"] or "",
        })

    return output_rows


def _empty_result(source_id: Any, raw_title: str, reason: str) -> Dict[str, Any]:
    return {
        "source_id": source_id,
        "source_title": raw_title,
        "match_status": "error",
        "match_confidence": 0,
        "match_reason": reason,
        "tmdb_id": "", "tmdb_title": "", "release_date": "",
        "runtime_minutes": "", "genres": "", "budget": "",
        "revenue": "", "vote_average": "", "popularity": "",
        "original_language": "",
    }
