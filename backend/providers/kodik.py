"""
kodik.py — Kodik CDN balancer provider

Kodik (kodikapi.com) aggregates licensed RU/CIS content and serves
iframe-embeddable players. Results carry embed URLs; the frontend
renders them inside an <iframe> — no stream extraction needed.

Environment variables:
    KODIK_TOKEN — API token from https://kodik.biz/api-docs
"""

from __future__ import annotations
import logging
import os
import httpx
from .base import BaseProvider, SearchResult, StreamInfo

logger = logging.getLogger("kinovibe.providers.kodik")

KODIK_API = "https://kodikapi.com"

_KIND_MAP = {
    "movies": "movie",
    "series": "foreign-serial,russian-serial",
    "anime": "anime-serial",
    "shorts": "movie",
}


class KodikProvider(BaseProvider):
    name = "kodik"
    source_type = "embed"

    @property
    def enabled(self) -> bool:
        return bool(os.environ.get("KODIK_TOKEN", ""))

    async def search(self, query: str, category: str) -> list[SearchResult]:
        token = os.environ.get("KODIK_TOKEN", "")
        if not token:
            return []
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    f"{KODIK_API}/search",
                    data={
                        "token": token,
                        "title": query,
                        "with_material_data": "true",
                        "limit": "10",
                        "types": _KIND_MAP.get(category, "movie"),
                    },
                )
            data = r.json()
            results = []
            for item in data.get("results", []):
                material = item.get("material_data", {})
                link = item.get("link", "")
                embed_url = ("https:" + link) if link.startswith("//") else link
                thumbnail = (
                    material.get("poster_url")
                    or material.get("kinopoisk_poster")
                    or material.get("screenshots", [None])[0]
                )
                results.append(SearchResult(
                    id=item.get("id", ""),
                    title=item.get("title") or item.get("title_orig", ""),
                    url=embed_url,
                    thumbnail=thumbnail,
                    duration=None,
                    channel="kodik",
                    provider=self.name,
                    source_type=self.source_type,
                    extra={
                        "year": item.get("year"),
                        "type": item.get("type"),
                        "kinopoisk_id": item.get("kinopoisk_id"),
                        "imdb_id": item.get("imdb_id"),
                    },
                ))
            return results
        except Exception as e:
            logger.error(f"Kodik search failed: {e}")
            return []

    async def get_stream(self, url: str) -> StreamInfo:
        # Kodik uses obfuscated iframe players; return embed protocol
        # so the frontend can render an <iframe src="url">
        return StreamInfo(
            stream_url=url,
            provider=self.name,
            protocol="embed",
        )
