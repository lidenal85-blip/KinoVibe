"""
yts_provider.py — YTS movie database provider
Uses movies-api.accel.li (YTS public API, no key needed)
Fallback: RapidAPI yts-am-torrent with API key

YTS = English-language Hollywood films, 720p/1080p/2160p
Magnets built from hash + announce trackers
"""
from __future__ import annotations

import logging
import os
import re

import httpx

from .base import BaseProvider, SearchResult

logger = logging.getLogger("kinovibe.providers.yts")

_YTS_DIRECT  = "https://movies-api.accel.li/api/v2"
_YTS_RAPIDAPI = "https://yts-am-torrent.p.rapidapi.com"
_RAPIDAPI_KEY = os.getenv("YTS_RAPIDAPI_KEY", "a97aec428amsh7dcfdcfd14f8f16p134ea8jsncbcdc8d430e8")

_TRACKERS = (
    "&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce"
    "&tr=udp%3A%2F%2Fopen.demonii.com%3A1337%2Fannounce"
    "&tr=udp%3A%2F%2Ftracker.torrent.eu.org%3A451%2Fannounce"
    "&tr=udp%3A%2F%2Fexodus.desync.com%3A6969%2Fannounce"
)

_QUALITY_PREF = ["2160p", "1080p.10bit", "1080p", "720p"]


def _build_magnet(torrent_hash: str, title: str) -> str:
    name = title.replace(" ", "+").replace("'", "")
    return f"magnet:?xt=urn:btih:{torrent_hash}&dn={name}{_TRACKERS}"


def _best_torrent(torrents: list[dict]) -> dict | None:
    """Pick best quality with seeds > 0, prefer higher quality."""
    alive = [t for t in torrents if t.get("seeds", 0) > 0]
    if not alive:
        # Fall back to any torrent if none have seeds
        alive = torrents
    if not alive:
        return None
    # Sort by quality preference
    def quality_rank(t: dict) -> int:
        q = t.get("quality", "")
        try:
            return _QUALITY_PREF.index(q)
        except ValueError:
            return len(_QUALITY_PREF)
    alive.sort(key=quality_rank)
    return alive[0]


async def _search_yts(query: str, limit: int = 8) -> list[dict]:
    """Search YTS API — tries direct URL first, falls back to RapidAPI."""
    params = {"query_term": query, "limit": limit, "sort_by": "seeds"}
    headers_direct = {"User-Agent": "KinoVibe/5.0"}
    headers_rapid  = {
        "x-rapidapi-host": "yts-am-torrent.p.rapidapi.com",
        "x-rapidapi-key":  _RAPIDAPI_KEY,
    }

    async with httpx.AsyncClient(follow_redirects=True, timeout=12) as c:
        # Try direct first
        try:
            r = await c.get(f"{_YTS_DIRECT}/list_movies.json", params=params, headers=headers_direct)
            if r.status_code == 200:
                return r.json().get("data", {}).get("movies", [])
        except Exception as e:
            logger.debug(f"[YTS] direct failed: {e}")

        # Fall back to RapidAPI
        try:
            r = await c.get(f"{_YTS_RAPIDAPI}/list_movies.json", params=params, headers=headers_rapid)
            if r.status_code == 200:
                return r.json().get("data", {}).get("movies", [])
        except Exception as e:
            logger.debug(f"[YTS] rapidapi failed: {e}")

    return []


class YTSProvider(BaseProvider):
    name = "yts"
    source_type = "magnet"

    async def search(self, query: str, category: str, max_results: int = 6) -> list[SearchResult]:
        movies = await _search_yts(query, limit=max_results * 2)
        results = []
        for m in movies:
            torrents = m.get("torrents", [])
            if not torrents:
                continue
            best = _best_torrent(torrents)
            if not best:
                continue

            title = m.get("title_long", m.get("title", query))
            quality = best.get("quality", "?")
            size    = best.get("size", "")
            seeds   = best.get("seeds", 0)
            rating  = m.get("rating", 0)
            year    = m.get("year", "")
            magnet  = _build_magnet(best["hash"], title)
            cover   = m.get("medium_cover_image") or m.get("small_cover_image")

            label = f"{title} [{quality}] {size}"
            if seeds > 0:
                label += f" 🌱{seeds}"

            yts_page = m.get("url", "")  # e.g. https://yts.bz/movies/...
            results.append(SearchResult(
                id=str(m.get("id", "")),
                title=label,
                url=magnet,
                thumbnail=cover,
                duration=m.get("runtime", 0) * 60 if m.get("runtime") else None,
                channel=f"YTS",
                provider=self.name,
                source_type="magnet",
                description=m.get("summary", ""),
                source_title=f"{title} [{quality}] {size}",
                source_url=yts_page,
            ))

            if len(results) >= max_results:
                break

        logger.info(f"[YTS] query={query!r} → {len(results)} results")
        return results

    async def get_stream(self, url: str):
        from .base import StreamInfo
        return StreamInfo(stream_url=url, provider=self.name, protocol="magnet")
