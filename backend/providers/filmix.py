from __future__ import annotations
import logging

import httpx

from .base import BaseProvider, SearchResult, StreamInfo

logger = logging.getLogger("kinovibe.providers.filmix")

_SEARCH_URLS = [
    "https://filmix.biz/api/movies",
    "https://filmix.ac/api/movies",
]


class FilmixProvider(BaseProvider):
    name = "filmix"
    source_type = "embed"

    @property
    def enabled(self) -> bool:
        return True

    async def search(self, query: str, category: str) -> list[SearchResult]:
        params = {"s": query}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        data = None
        for base_url in _SEARCH_URLS:
            try:
                async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                    r = await client.get(base_url, params=params, headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    break
            except Exception as e:
                logger.debug(f"Filmix search attempt {base_url} failed: {e}")
                continue

        if data is None:
            return []

        results: list[SearchResult] = []
        # API may return a list directly or wrapped in a key
        items = data if isinstance(data, list) else data.get("results", data.get("items", []))
        for item in items:
            if not isinstance(item, dict):
                continue
            vid_id = item.get("id")
            if not vid_id:
                continue
            embed_url = f"https://filmix.biz/embed/{vid_id}"
            results.append(SearchResult(
                id=str(vid_id),
                title=item.get("title") or item.get("name") or "Без названия",
                url=embed_url,
                thumbnail=item.get("poster_url") or item.get("poster"),
                duration=None,
                channel=None,
                provider=self.name,
                source_type=self.source_type,
                extra={"description": item.get("description") or ""},
            ))
        return results

    async def get_stream(self, url: str) -> StreamInfo:
        return StreamInfo(
            stream_url=url,
            embed_url=url,
            provider=self.name,
            protocol="embed",
        )
