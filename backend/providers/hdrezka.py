"""
hdrezka.py — HDRezka scraping provider

HDRezka (rezka.ag) is a popular RU movie/series aggregator. This provider
scrapes the public search page and returns movie page URLs.

Stream extraction: tries yt-dlp first; if that fails, returns the page URL
as an iframe embed (watch_screen renders it via HtmlElementView IFrame).

Environment variables:
    HDREZKA_ENABLED  — set to "0" to disable (default: enabled)
    HDREZKA_BASE_URL — override base URL (default: https://rezka.ag)
"""

from __future__ import annotations
import asyncio
import logging
import os
import re
import subprocess
from typing import Optional
import httpx
from .base import BaseProvider, SearchResult, StreamInfo

logger = logging.getLogger("kinovibe.providers.hdrezka")

HDREZKA_BASE = os.environ.get("HDREZKA_BASE_URL", "https://rezka.ag")
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
YTDLP = os.path.join(_BACKEND_DIR, "venv", "bin", "yt-dlp")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

_CARD_RE = re.compile(
    r'<div[^>]+class="b-content__inline_item"[^>]+data-url="([^"]+)"[^>]*>.*?'
    r'<img[^>]+src="([^"]+)"[^>]*>.*?'
    r'b-content__inline_item-link[^>]*>.*?'
    r'<a[^>]*>([^<]+)</a>',
    re.DOTALL | re.IGNORECASE,
)


class HDRezkaProvider(BaseProvider):
    name = "hdrezka"
    source_type = "embed"

    @property
    def enabled(self) -> bool:
        return os.environ.get("HDREZKA_ENABLED", "1") != "0"

    async def search(self, query: str, category: str) -> list[SearchResult]:
        try:
            rus_query = f"русский озвучка {query}"
            async with httpx.AsyncClient(
                timeout=15, headers=_HEADERS, follow_redirects=True
            ) as c:
                r = await c.get(
                    f"{HDREZKA_BASE}/search/",
                    params={"do": "search", "subaction": "search", "q": rus_query},
                )
            if r.status_code != 200:
                logger.warning(f"HDRezka returned HTTP {r.status_code}")
                return []
            return self._parse_results(r.text)
        except Exception as e:
            logger.error(f"HDRezka search failed: {e}")
            return []

    def _parse_results(self, html: str) -> list[SearchResult]:
        results = []
        for m in _CARD_RE.finditer(html):
            url = m.group(1).strip()
            thumbnail = m.group(2).strip()
            title = m.group(3).strip()
            slug = url.rstrip("/").split("/")[-1]
            results.append(SearchResult(
                id=slug,
                title=title,
                url=url,
                thumbnail=thumbnail,
                provider=self.name,
                source_type=self.source_type,
            ))
            if len(results) >= 10:
                break
        return results

    async def get_stream(self, url: str) -> StreamInfo:
        # Try yt-dlp first for a direct playable URL
        try:
            loop = asyncio.get_running_loop()
            stream_url = await loop.run_in_executor(None, self._ytdlp_extract, url)
            if stream_url:
                logger.info(f"[HDREZKA] yt-dlp extracted direct stream: {stream_url[:60]}")
                return StreamInfo(stream_url=stream_url, provider=self.name, protocol="http")
        except Exception as e:
            logger.debug(f"[HDREZKA] yt-dlp failed ({e}), falling back to iframe embed")

        # Fallback: serve the page as an iframe embed
        return StreamInfo(
            stream_url="",
            embed_url=url,
            provider=self.name,
            protocol="embed",
        )

    def _ytdlp_extract(self, url: str) -> Optional[str]:
        proc = subprocess.run(
            [
                YTDLP, "-g",
                "-f", "best[ext=mp4]/best",
                "--no-playlist",
                "--add-header", "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "--add-header", f"Referer:{HDREZKA_BASE}/",
                "--add-header", "Accept-Language:ru-RU,ru;q=0.9,en;q=0.8",
                url,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip().splitlines()[0]
        logger.debug(f"[HDREZKA] yt-dlp stderr: {proc.stderr[:300]}")
        return None
