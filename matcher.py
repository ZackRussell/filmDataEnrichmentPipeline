"""
matcher.py

Resolves an arbitrary (title, optional_date) pair from a user's dataset to a
specific TMDB movie record.

Why this is non-trivial:
  - Titles are rarely a perfect string match (remakes, punctuation, "The",
    international titles, year suffixes someone added by hand, etc.)
  - A date column in someone's source data is NOT guaranteed to mean
    "release year." It might be an awards ceremony year, a film/eligibility
    year, a re-release year, or something else entirely. This pipeline does
    not assume it knows what a date column means — it uses it only as a
    soft disambiguator, never as a hard filter, and logs its reasoning.

Matching strategy:
  1. Search TMDB by title (+ year, if provided, as a search hint only).
  2. Score every candidate on title similarity (rapidfuzz) AND, if a date
     was provided, proximity between that date and the candidate's TMDB
     release year (within a tolerance window).
  3. Pick the best-scoring candidate above a minimum confidence threshold.
  4. If no candidate clears the threshold, the row is logged as unmatched
     rather than guessed.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from rapidfuzz import fuzz

# How many years apart a provided date and TMDB's release year can be before
# we stop trusting the date as a disambiguator. Generous, on purpose — award
# ceremony year vs. film year vs. release year easily differ by 1-2 years,
# and re-releases / festival vs. wide release can stretch this further.
DATE_TOLERANCE_YEARS = 3

# Minimum combined confidence score (0-100) required to accept a match.
MIN_MATCH_CONFIDENCE = 70


@dataclass
class MatchResult:
    matched: bool
    tmdb_id: Optional[int]
    tmdb_title: Optional[str]
    confidence: float
    reason: str
    candidates_considered: int


def _title_similarity(query_title: str, candidate_title: str) -> float:
    """Token-sort ratio handles word reordering and minor punctuation drift
    better than a plain string comparison (e.g. 'Lord of the Rings, The')."""
    return fuzz.token_sort_ratio(query_title.lower().strip(), candidate_title.lower().strip())


def _extract_year(date_str: Optional[str]) -> Optional[int]:
    if not date_str:
        return None
    try:
        return int(str(date_str)[:4])
    except (ValueError, TypeError):
        return None


def find_best_match(
    title: str,
    candidates: List[Dict[str, Any]],
    provided_year: Optional[int] = None,
) -> MatchResult:
    """
    candidates: raw TMDB /search/movie results (each has 'title',
    'release_date', 'id', etc.)
    provided_year: a year pulled from the user's source data, if any.
                   Treated as a SOFT signal only — see module docstring.
    """
    if not candidates:
        return MatchResult(
            matched=False, tmdb_id=None, tmdb_title=None,
            confidence=0.0, reason="no_candidates_returned", candidates_considered=0,
        )

    scored = []
    for c in candidates:
        candidate_title = c.get("title") or c.get("original_title") or ""
        title_score = _title_similarity(title, candidate_title)

        candidate_year = _extract_year(c.get("release_date"))
        date_bonus = 0.0
        date_note = "no_date_signal"

        if provided_year and candidate_year:
            year_gap = abs(provided_year - candidate_year)
            if year_gap == 0:
                date_bonus = 15.0
                date_note = "exact_year_match"
            elif year_gap <= DATE_TOLERANCE_YEARS:
                # Linear falloff within the tolerance window
                date_bonus = 15.0 * (1 - year_gap / (DATE_TOLERANCE_YEARS + 1))
                date_note = f"year_within_tolerance(gap={year_gap})"
            else:
                date_note = f"year_outside_tolerance(gap={year_gap})"

        combined = min(100.0, title_score + date_bonus)
        scored.append((combined, title_score, date_bonus, date_note, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    best_combined, best_title_score, best_date_bonus, best_date_note, best_candidate = scored[0]

    if best_combined < MIN_MATCH_CONFIDENCE:
        return MatchResult(
            matched=False, tmdb_id=None, tmdb_title=None,
            confidence=round(best_combined, 1),
            reason=f"best_candidate_below_threshold(title_score={best_title_score:.1f}, {best_date_note})",
            candidates_considered=len(candidates),
        )

    return MatchResult(
        matched=True,
        tmdb_id=best_candidate.get("id"),
        tmdb_title=best_candidate.get("title"),
        confidence=round(best_combined, 1),
        reason=f"title_score={best_title_score:.1f}, {best_date_note}",
        candidates_considered=len(candidates),
    )
