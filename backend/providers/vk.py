"""
vk.py — VK Video provider

Search strategy:
  1. VK API (preferred) — requires VK_TOKEN env var.
     Uses video.search, returns embed player URLs (source_type="embed").
  2. Web scraping fallback — httpx GET https://vk.com/video?q={query}&search_own=0
     Parses embedded JSON payload from the server-rendered page.

Stream extraction: embed iframe for API results; yt-dlp fallback for direct URLs.

Environment variables:
    VK_TOKEN — VK API access token (os.getenv("VK_TOKEN"))
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

# Regex patterns for web scraping fallback
_VIDEO_ID_RE = re.compile(r'"(-?\d{5,})_(\d{5,})"')
_TITLE_RE = re.compile(r'"title"\s*:\s*"([^"]{3,80})"')
_THUMB_RE = re.compile(r'"photo_800"\s*:\s*"([^"]+)"|"photo_640"\s*:\s*"([^"]+)"|"photo_320"\s*:\s*"([^"]+)"')
_DURATION_RE = re.compile(r'"duration"\s*:\s*(\d+)')


def _pick_thumbnail(image_list: list) -> str | None:
    """Pick the largest available thumbnail from VK image array."""
    if not image_list:
        return None
    # VK returns sorted list; last entry is usually largest
    for img in reversed(image_list):
        if isinstance(img, dict) and img.get("url"):
            return img["url"]
    return None


class VKProvider(BaseProvider):
    name = "vk"
    source_type = "embed"

    @property
    def enabled(self) -> bool:
        return True

    async def search(self, query: str, category: str, vk_token: str | None = None) -> list[SearchResult]:
        token = vk_token or os.getenv("VK_TOKEN", "") or os.environ.get("VK_API_TOKEN", "")
        if token:
            src = "user" if vk_token else "global"
            logger.debug(f"VK: using {src} token")
            results = await self._search_api(query, token)
            if results:
                return results
            # API returned nothing — try web scraping as last resort
            return await self._search_web(query)
        # No token: VK API requires auth since 2024, web scraping redirects to vkvideo.ru login
        logger.info("VK: no VK_TOKEN set — skipping (set VK_TOKEN env var to enable VK search)")
        return []

    # ── VK API search ────────────────────────────────────────────────────────

    async def _search_api(self, query: str, token: str) -> list[SearchResult]:
        """Search via VK API video.search; returns embed-URL results."""
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
                    },
                )
            data = r.json()

            if "error" in data:
                code = data["error"].get("error_code", 0)
                msg = data["error"].get("error_msg", "")
                logger.warning(f"VK API error {code}: {msg}")
                return []

            items = data.get("response", {}).get("items", [])
            results = []
            for item in items:
                player_url = item.get("player", "")
                if not player_url:
                    continue

                vid_id = f"{item.get('owner_id', '')}_{item.get('id', '')}"
                thumbnail = _pick_thumbnail(item.get("image", []))

                results.append(SearchResult(
                    id=vid_id,
                    title=item.get("title", ""),
                    url=player_url,
                    thumbnail=thumbnail,
                    duration=item.get("duration"),
                    channel=str(item.get("owner_id", "")),
                    provider=self.name,
                    source_type="embed",
                ))

            logger.info(f"VK API: {len(results)} results for '{query}'")
            return results

        except Exception as e:
            logger.error(f"VK API search failed: {e}")
            return []

    # ── Web scraping fallback ────────────────────────────────────────────────

    async def _search_web(self, query: str) -> list[SearchResult]:
        """Scrape https://vk.com/video?q={query} without token."""
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

                # Build embed URL from owner/video id
                embed_url = f"https://vk.com/video_ext.php?oid={owner_id}&id={video_id}"

                results.append(SearchResult(
                    id=vid_key,
                    title=title,
                    url=embed_url,
                    thumbnail=thumbnail,
                    duration=duration,
                    channel=owner_id,
                    provider=self.name,
                    source_type="embed",
                ))
                if len(results) >= 10:
                    break

            logger.info(f"VK web scrape: {len(results)} results for '{query}'")
            return results

        except Exception as e:
            logger.error(f"VK web search failed: {e}")
            return []

    # ── Stream extraction ────────────────────────────────────────────────────

    async def get_stream(self, url: str) -> StreamInfo:
        """Embed URLs are played as iframes; direct vk.com URLs use yt-dlp."""
        if "video_ext.php" in url or url.startswith("https://vk.com/video_ext"):
            # Already an embed URL — return as-is for iframe playback
            return StreamInfo(
                stream_url=url,
                provider=self.name,
                protocol="embed",
            )
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
            logger.error(f"VK yt-dlp stream failed: {e}")
        raise ValueError(f"Could not extract VK stream for {url}")
