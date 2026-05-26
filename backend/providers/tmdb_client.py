"""
tmdb_client.py — TMDB API wrapper
Returns rich film metadata: poster, rating, description, genres, year.
Used as single source of truth for film cards.
"""
from __future__ import annotations
import asyncio
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("kinovibe.tmdb")

_BASE   = "https://api.themoviedb.org/3"
_IMG    = "https://image.tmdb.org/t/p/w500"
_APIKEY = os.environ.get("TMDB_API_KEY", "")

_HEADERS = {
    "Authorization": f"Bearer {_APIKEY}",
    "Accept": "application/json",
}


def _poster(path: Optional[str]) -> Optional[str]:
    return f"{_IMG}{path}" if path else None


async def search_movie(
    query: str,
    year: Optional[int] = None,
    language: str = "ru-RU",
    limit: int = 5,
) -> list[dict]:
    """Search TMDB for movies matching query. Returns enriched dicts."""
    if not _APIKEY:
        return []
    params = {"query": query, "language": language, "page": 1, "include_adult": False}
    if year:
        params["year"] = year

    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(f"{_BASE}/search/movie", params=params, headers=_HEADERS)
        if r.status_code != 200:
            logger.warning(f"[TMDB] search {query!r} → HTTP {r.status_code}")
            return []
        results = r.json().get("results", [])
    except Exception as e:
        logger.warning(f"[TMDB] search error: {e}")
        return []

    return [_normalize(m) for m in results[:limit]]


async def get_movie(tmdb_id: int, language: str = "ru-RU") -> Optional[dict]:
    """Get full movie details by TMDB ID."""
    if not _APIKEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(
                f"{_BASE}/movie/{tmdb_id}",
                params={"language": language, "append_to_response": "credits"},
                headers=_HEADERS,
            )
        if r.status_code != 200:
            return None
        return _normalize(r.json())
    except Exception as e:
        logger.warning(f"[TMDB] get_movie {tmdb_id} error: {e}")
        return None


async def search_and_enrich(title: str, year: Optional[int] = None) -> Optional[dict]:
    """Find the best TMDB match for a film title."""
    results = await search_movie(title, year=year, limit=1)
    if results:
        return results[0]
    # Try without year
    if year:
        results = await search_movie(title, limit=1)
        if results:
            return results[0]
    return None


def _normalize(m: dict) -> dict:
    """Convert TMDB movie dict to our standard format."""
    genres = [g["name"] for g in m.get("genres", [])] or \
             [str(g) for g in m.get("genre_ids", [])][:3]
    year = (m.get("release_date") or "")[:4]
    runtime = m.get("runtime")  # minutes

    return {
        "tmdb_id":     m.get("id"),
        "title":       m.get("title") or m.get("name", ""),
        "title_orig":  m.get("original_title") or m.get("original_name", ""),
        "year":        year,
        "rating":      round(m.get("vote_average", 0), 1),
        "votes":       m.get("vote_count", 0),
        "poster":      _poster(m.get("poster_path")),
        "backdrop":    _poster(m.get("backdrop_path")),
        "description": m.get("overview", ""),
        "genres":      genres,
        "duration":    runtime * 60 if runtime else None,
        "popularity":  m.get("popularity", 0),
        "adult":       m.get("adult", False),
        "media_type":  "movie",
    }


def _normalize_tv(t: dict) -> dict:
    """Convert TMDB TV show dict to our standard format."""
    genres = [g["name"] for g in t.get("genres", [])] or \
             [str(g) for g in t.get("genre_ids", [])][:3]
    year = (t.get("first_air_date") or "")[:4]
    return {
        "tmdb_id":     t.get("id"),
        "title":       t.get("name") or t.get("original_name", ""),
        "title_orig":  t.get("original_name", ""),
        "year":        year,
        "rating":      round(t.get("vote_average", 0), 1),
        "votes":       t.get("vote_count", 0),
        "poster":      _poster(t.get("poster_path")),
        "backdrop":    _poster(t.get("backdrop_path")),
        "description": t.get("overview", ""),
        "genres":      genres,
        "duration":    None,
        "popularity":  t.get("popularity", 0),
        "adult":       t.get("adult", False),
        "media_type":  "tv",
    }


async def search_tv(
    query: str,
    language: str = "ru-RU",
    limit: int = 5,
) -> list[dict]:
    """Search TMDB for TV shows matching query."""
    if not _APIKEY:
        return []
    params = {"query": query, "language": language, "page": 1, "include_adult": False}
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(f"{_BASE}/search/tv", params=params, headers=_HEADERS)
        if r.status_code != 200:
            logger.warning(f"[TMDB] search_tv {query!r} → HTTP {r.status_code}")
            return []
        results = r.json().get("results", [])
    except Exception as e:
        logger.warning(f"[TMDB] search_tv error: {e}")
        return []
    return [_normalize_tv(t) for t in results[:limit]]


async def get_tv_seasons(tmdb_id: int, language: str = "ru-RU") -> list[dict]:
    """Get seasons list for a TV show (skips specials season 0)."""
    if not _APIKEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(
                f"{_BASE}/tv/{tmdb_id}",
                params={"language": language},
                headers=_HEADERS,
            )
        if r.status_code != 200:
            return []
        data = r.json()
        seasons = [
            {
                "season_number": s["season_number"],
                "episode_count": s.get("episode_count", 0),
                "name":          s.get("name", f"Сезон {s['season_number']}"),
                "air_date":      (s.get("air_date") or "")[:4],
                "poster":        _poster(s.get("poster_path")),
            }
            for s in data.get("seasons", [])
            if s.get("season_number", 0) > 0  # skip specials
        ]
        return seasons
    except Exception as e:
        logger.warning(f"[TMDB] get_tv_seasons {tmdb_id} error: {e}")
        return []


async def get_tv_season_episodes(
    tmdb_id: int,
    season_number: int,
    language: str = "ru-RU",
) -> list[dict]:
    """Get episodes for a specific TV season."""
    if not _APIKEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(
                f"{_BASE}/tv/{tmdb_id}/season/{season_number}",
                params={"language": language},
                headers=_HEADERS,
            )
        if r.status_code != 200:
            return []
        data = r.json()
        episodes = [
            {
                "episode_number": ep["episode_number"],
                "name":           ep.get("name", ""),
                "air_date":       (ep.get("air_date") or "")[:4],
                "overview":       ep.get("overview", ""),
                "rating":         round(ep.get("vote_average", 0), 1),
                "still":          f"https://image.tmdb.org/t/p/w300{ep['still_path']}"
                                  if ep.get("still_path") else None,
            }
            for ep in data.get("episodes", [])
        ]
        return episodes
    except Exception as e:
        logger.warning(f"[TMDB] get_tv_season_episodes {tmdb_id}/{season_number} error: {e}")
        return []
