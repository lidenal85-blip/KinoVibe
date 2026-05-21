from __future__ import annotations
import asyncio
import json
import logging
import os
import subprocess
from .base import BaseProvider, SearchResult, StreamInfo

logger = logging.getLogger("kinovibe.providers.youtube")

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
YTDLP = os.path.join(_BACKEND_DIR, "venv", "bin", "yt-dlp")


class YouTubeProvider(BaseProvider):
    name = "youtube"
    source_type = "video"

    async def search(self, query: str, category: str, max_results: int = 8) -> list[SearchResult]:
        loop = asyncio.get_running_loop()
        rus_query = f"русский озвучка {query} смотреть онлайн"
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, self._ytdlp_search, rus_query, max_results),
                timeout=18.0,
            )
        except asyncio.TimeoutError:
            logger.warning(f"YouTube search timed out for: {query!r}")
            return []

    def _ytdlp_search(self, query: str, max_results: int) -> list[SearchResult]:
        cmd = [YTDLP, "--dump-json", "--no-playlist", "--skip-download",
               f"ytsearch{max_results}:{query}"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            results = []
            for line in proc.stdout.splitlines():
                try:
                    item = json.loads(line)
                    results.append(SearchResult(
                        id=item.get("id", ""),
                        title=item.get("title", ""),
                        url=item.get("webpage_url", ""),
                        thumbnail=item.get("thumbnail"),
                        duration=item.get("duration"),
                        channel=item.get("uploader"),
                        provider=self.name,
                        source_type=self.source_type,
                    ))
                except Exception:
                    continue
            return results
        except Exception as e:
            logger.error(f"yt-dlp search failed: {e}")
            return []

    async def get_stream(self, url: str) -> StreamInfo:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._ytdlp_stream, url)

    def _ytdlp_stream(self, url: str) -> StreamInfo:
        # Форсируем mp4/m4a, отключаем проверку сертификатов и плейлисты для максимального ускорения
        cmd = [
            YTDLP, 
            "-g", 
            "-f", "best[ext=mp4]/best", 
            "--no-playlist",
            "--no-check-certificate",
            "--socket-timeout", "10",
            url
        ]
        try:
            # Уменьшаем таймаут до 20 секунд, чтобы бэкенд успел ответить ошибкой, 
            # если yt-dlp намертво зависнет, не доводя фронтенд до TimeoutException
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if proc.returncode == 0 and proc.stdout.strip():
                lines = proc.stdout.strip().splitlines()
                return StreamInfo(
                    stream_url=lines[0],
                    audio_url=lines[1] if len(lines) > 1 else None,
                    provider=self.name,
                    protocol="http",
                )
            else:
                logger.error(f"yt-dlp stream extraction failed code={proc.returncode}. Error: {proc.stderr}")
        except subprocess.TimeoutExpired:
            logger.error(f"yt-dlp subprocess timed out for URL: {url}")
        except Exception as e:
            logger.error(f"yt-dlp stream failed: {e}")
            
        raise ValueError(f"Could not extract stream within safe timeout limits")
