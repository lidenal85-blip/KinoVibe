from __future__ import annotations
import asyncio
import logging
import os
import subprocess

import httpx

from .base import BaseProvider, SearchResult, StreamInfo

logger = logging.getLogger("kinovibe.providers.rutube")

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
YTDLP = os.path.join(_BACKEND_DIR, "venv", "bin", "yt-dlp")


class RutubeProvider(BaseProvider):
    name = "rutube"
    source_type = "video"

    @property
    def enabled(self) -> bool:
        return True

    async def search(self, query: str, category: str) -> list[SearchResult]:
        url = "https://rutube.ru/api/search/video/"
        params = {"query": query, "page": 1, "format": "json"}
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                r = await client.get(url, params=params,
                                     headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                logger.warning(f"Rutube search returned {r.status_code}")
                return []
            data = r.json()
        except Exception as e:
            logger.warning(f"Rutube search error: {e}")
            return []

        results: list[SearchResult] = []
        items = data.get("results") or data.get("items") or []
        for item in items:
            vid_id = item.get("id", "")
            if not vid_id:
                continue
            video_url = f"https://rutube.ru/video/{vid_id}/"
            duration_raw = item.get("duration")
            try:
                duration = int(duration_raw) if duration_raw is not None else None
            except (ValueError, TypeError):
                duration = None
            author = item.get("author") or {}
            channel = author.get("name") if isinstance(author, dict) else None
            results.append(SearchResult(
                id=str(vid_id),
                title=item.get("title", ""),
                url=video_url,
                thumbnail=item.get("thumbnail_url"),
                duration=duration,
                channel=channel,
                provider=self.name,
                source_type=self.source_type,
            ))
        return results

    async def get_stream(self, url: str) -> StreamInfo:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._ytdlp_stream, url)

    def _ytdlp_stream(self, url: str) -> StreamInfo:
        cmd = [
            YTDLP,
            "-g",
            "-f", "best[ext=mp4]/best",
            "--no-playlist",
            "--no-check-certificate",
            "--socket-timeout", "10",
            url,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if proc.returncode == 0 and proc.stdout.strip():
                lines = proc.stdout.strip().splitlines()
                return StreamInfo(
                    stream_url=lines[0],
                    audio_url=lines[1] if len(lines) > 1 else None,
                    provider=self.name,
                    protocol="http",
                )
            logger.error(f"yt-dlp rutube stream failed code={proc.returncode}: {proc.stderr[:200]}")
        except subprocess.TimeoutExpired:
            logger.error(f"yt-dlp rutube timed out for: {url}")
        except Exception as e:
            logger.error(f"yt-dlp rutube error: {e}")
        raise ValueError("Could not extract Rutube stream")
