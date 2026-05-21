"""
vk.py — VK Video provider

Two search strategies:
  1. API (preferred) — requires VK_API_TOKEN env var.
  2. Web scraping — httpx GET https://vk.com/video?q={query}&search_own=0
     Parses embedded JSON payload from the server-rendered page. VK renders
     some video metadata server-side even for unauthenticated requests.

Stream extraction uses yt-dlp in both cases.

Environment variables:
    VK_API_TOKEN — VK API access token (optional; enables API search)
"""

from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import subprocess
from urllib.parse import quote
import httpx
from .base import BaseProvider, SearchResult, StreamInfo

logger = logging.getLogger("kinovibe.providers.vk")

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
YTDLP = os.path.join(_BACKEND_DIR, "venv", "bin", "yt-dlp")

VK_TOKEN = os.environ.get("VK_API_TOKEN", "")
VK_API = "https://api.vk.com/method"
VK_API_VERSION = "5.131"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Regex to pull embedded JSON arrays from VK's page source
_VK_JSON_RE = re.compile(r'\["video",\s*\{.*?\}\]', re.DOTALL)
# Simpler: look for ownerid_videoid pairs in any form
_VIDEO_ID_RE = re.compile(r'"(-?\d{5,})_(\d{5,})"')
_TITLE_RE = re.compile(r'"title"\s*:\s*"([^"]{3,80})"')
_THUMB_RE = re.compile(r'"photo_800"\s*:\s*"([^"]+)"|"photo_640"\s*:\s*"([^"]+)"|"photo_320"\s*:\s*"([^"]+)"')
_DURATION_RE = re.compile(r'"duration"\s*:\s*(\d+)')


class VKProvider(BaseProvider):
    name = "vk"
    source_type = "video"

    @property
    def enabled(self) -> bool:
        # Always enabled — API token enables richer results, web fallback works without it
        return True

    async def search(self, query: str, category: str) -> list[SearchResult]:
        token = os.environ.get("VK_API_TOKEN", "")
        if token:
            results = await self._search_api(query, token)
            if results:
                return results
        # Fallback: web scraping
        return await self._search_web(query)

    async def _search_api(self, query: str, token: str) -> list[SearchResult]:
        try:
            rus_query = f"русский озвучка {query}"
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    f"{VK_API}/video.search",
                    params={
                        "q": rus_query,
                        "access_token": token,
                        "v": VK_API_VERSION,
                        "count": 10,
                        "hd": 1,
                        "filters": "mp4",
                    },
                )
            data = r.json()
            if "error" in data:
                logger.warning(f"VK API error: {data['error']}")
                return []
            items = data.get("response", {}).get("items", [])
            results = []
            for item in items:
                vid_id = f"{item['owner_id']}_{item['id']}"
                thumbnail = (
                    item.get("photo_800")
                    or item.get("photo_640")
                    or item.get("photo_320")
                )
                results.append(SearchResult(
                    id=vid_id,
                    title=item.get("title", ""),
                    url=f"https://vk.com/video{vid_id}",
                    thumbnail=thumbnail,
                    duration=item.get("duration"),
                    channel=str(item.get("owner_id", "")),
                    provider=self.name,
                    source_type=self.source_type,
                ))
            return results
        except Exception as e:
            logger.error(f"VK API search failed: {e}")
            return []

    async def _search_web(self, query: str) -> list[SearchResult]:
        """Search via https://vk.com/video?q={query}&search_own=0 without token."""
        try:
            url = f"https://vk.com/video?q={quote(query)}&search_own=0"
            async with httpx.AsyncClient(
                timeout=15, headers=_HEADERS, follow_redirects=True
            ) as c:
                r = await c.get(url)

            if r.status_code != 200:
                logger.warning(f"VK web search returned HTTP {r.status_code}")
                return []

            html = r.text
            # Extract video id pairs and try to match with titles/thumbnails
            id_matches = _VIDEO_ID_RE.findall(html)
            title_matches = _TITLE_RE.findall(html)
            thumb_matches = _THUMB_RE.findall(html)
            duration_matches = _DURATION_RE.findall(html)

            results = []
            seen: set[str] = set()
            for i, (owner_id, video_id) in enumerate(id_matches):
                vid_key = f"{owner_id}_{video_id}"
                if vid_key in seen:
                    continue
                seen.add(vid_key)

                title = title_matches[i] if i < len(title_matches) else f"VK Video {vid_key}"
                thumb_groups = thumb_matches[i] if i < len(thumb_matches) else ("", "", "")
                thumbnail = next((g for g in thumb_groups if g), None)
                duration = int(duration_matches[i]) if i < len(duration_matches) else None

                results.append(SearchResult(
                    id=vid_key,
                    title=title,
                    url=f"https://vk.com/video{vid_key}",
                    thumbnail=thumbnail,
                    duration=duration,
                    channel=owner_id,
                    provider=self.name,
                    source_type=self.source_type,
                ))
                if len(results) >= 10:
                    break

            logger.info(f"VK web search: found {len(results)} results for '{query}'")
            return results
        except Exception as e:
            logger.error(f"VK web search failed: {e}")
            return []

    async def get_stream(self, url: str) -> StreamInfo:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._ytdlp_stream, url)

    def _ytdlp_stream(self, url: str) -> StreamInfo:
        try:
            proc = subprocess.run(
                [YTDLP, "-g", "-f", "best[ext=mp4]/best", "--no-playlist", url],
                capture_output=True, text=True, timeout=30,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return StreamInfo(
                    stream_url=proc.stdout.strip().splitlines()[0],
                    provider=self.name,
                    protocol="http",
                )
        except Exception as e:
            logger.error(f"VK stream extraction failed: {e}")
        raise ValueError(f"Could not extract VK stream for {url}")
