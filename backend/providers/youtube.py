from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import subprocess
from .base import BaseProvider, SearchResult, StreamInfo

logger = logging.getLogger("kinovibe.providers.youtube")

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
YTDLP = os.path.join(_BACKEND_DIR, "venv", "bin", "yt-dlp")

# Words in title that always indicate a promo/trailer — filtered regardless of category
_TRAILER_RE = re.compile(
    r'\b(трейлер|trailer|тизер|teaser|promo|промо|анонс'
    r'|official\s+trailer|official\s+video|music\s+video|making\s+of|behind\s+the\s+scenes'
    r'|bts|interview|интервью|обзор|review|reaction|реакция|топ\s+\d|рейтинг)\b',
    re.IGNORECASE,
)

# Query/category tokens that signal short-film mode
_SHORT_RE = re.compile(
    r'\b(короткометражк[аиу]?|short\s+film|short\s+films|shorts)\b',
    re.IGNORECASE,
)

_MIN_MOVIE_SECONDS  = 1200   # 20 min for regular content
_MIN_SHORT_SECONDS  = 60     # 1 min — even very short films are ≥1 min
_MAX_SHORT_SECONDS  = 2400   # 40 min upper cap for short films


def _is_short_mode(query: str, category: str) -> bool:
    return category == "shorts" or bool(_SHORT_RE.search(query))


def _is_unwanted(title: str, duration_sec: int | None, short_mode: bool) -> bool:
    """Return True if the result should be filtered out."""
    # Always drop trailers/promos by title keyword
    if _TRAILER_RE.search(title):
        return True
    if duration_sec is None:
        return False  # unknown duration — keep it, let the user decide
    if short_mode:
        # Short-film mode: 1 min – 40 min
        return duration_sec < _MIN_SHORT_SECONDS or duration_sec > _MAX_SHORT_SECONDS
    else:
        # Regular mode: must be ≥ 20 min
        return duration_sec < _MIN_MOVIE_SECONDS


class YouTubeProvider(BaseProvider):
    name = "youtube"
    source_type = "video"

    async def search(self, query: str, category: str, max_results: int = 8) -> list[SearchResult]:
        loop = asyncio.get_running_loop()
        short_mode = _is_short_mode(query, category)
        year_match = re.search(r'\b(19|20)\d{2}\b', query)
        if short_mode:
            rus_query = f"короткометражный фильм {query} смотреть онлайн"
        elif year_match:
            rus_query = f'"{query}" смотреть полностью русская озвучка'
        else:
            rus_query = f"русская озвучка {query} смотреть полностью фильм"
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(
                    None, self._ytdlp_search, rus_query, max_results + 6, short_mode
                ),
                timeout=22.0,
            )
        except asyncio.TimeoutError:
            logger.warning(f"YouTube search timed out for: {query!r}")
            return []

    def _ytdlp_search(self, query: str, max_results: int, short_mode: bool = False) -> list[SearchResult]:
        cmd = [
            YTDLP, "--flat-playlist", "--dump-single-json", "--no-playlist",
            "--extractor-args", "youtube:player_client=android",
            f"ytsearch{max_results}:{query}",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
            if proc.returncode != 0 or not proc.stdout.strip():
                logger.error(f"yt-dlp search rc={proc.returncode}: {proc.stderr[:200]}")
                return []
            data = json.loads(proc.stdout)
            entries = data.get("entries") or []
            results = []
            for item in entries:
                if not item:
                    continue
                title = item.get("title", "")
                duration = item.get("duration")
                if _is_unwanted(title, duration, short_mode):
                    logger.debug(f"[YT] Filtered: {title!r} ({duration}s, short={short_mode})")
                    continue
                vid_id = item.get("id", "")
                url = item.get("url") or item.get("webpage_url") or (
                    f"https://www.youtube.com/watch?v={vid_id}" if vid_id else ""
                )
                thumbnail = (
                    item.get("thumbnail")
                    or (item.get("thumbnails") or [{}])[-1].get("url")
                )
                results.append(SearchResult(
                    id=vid_id,
                    title=title,
                    url=url,
                    thumbnail=thumbnail,
                    duration=duration,
                    channel=item.get("uploader") or item.get("channel"),
                    provider=self.name,
                    source_type=self.source_type,
                ))
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
            "--extractor-args", "youtube:player_client=android",
            url,
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
