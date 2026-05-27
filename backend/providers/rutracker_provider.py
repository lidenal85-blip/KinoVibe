"""
rutracker_provider.py — Прямой поиск на RuTracker.org с авторизацией.

Авторизуется по логину/паролю, ищет раздачи, возвращает magnet-ссылки.
Сессия кешируется в памяти — один логин на весь процесс.

Environment variables:
    RUTRACKER_LOGIN    — логин на rutracker.org
    RUTRACKER_PASSWORD — пароль на rutracker.org
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Optional
from urllib.parse import quote, quote_plus

import httpx

from .base import BaseProvider, SearchResult
from .torrent_validator import validate_torrent

logger = logging.getLogger("kinovibe.providers.rutracker")

_LOGIN_URL  = "https://rutracker.org/forum/login.php"
_SEARCH_URL = "https://rutracker.org/forum/tracker.php"
_BASE       = "https://rutracker.org/forum"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://rutracker.org/",
}

_MAGNET_RE  = re.compile(r'magnet:\?xt=urn:btih:([a-fA-F0-9]{40})', re.I)
_TOPIC_RE   = re.compile(r'viewtopic\.php\?t=(\d+)')
_TITLE_RE   = re.compile(r'class="t-title"[^>]*>\s*(?:<[^>]+>)*([^<]+)')
_SEEDS_RE   = re.compile(r'class="seedmed"[^>]*>\s*(\d+)')
_SIZE_RE    = re.compile(r'(\d+[.,]?\d*)\s*&nbsp;(ГБ|МБ|GB|MB)', re.I)
_HASH_DL_RE = re.compile(r'data-topic_id="(\d+)"')

_TRACKERS = (
    "&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce"
    "&tr=udp%3A%2F%2Fopen.demonii.com%3A1337%2Fannounce"
    "&tr=udp%3A%2F%2Ftracker.torrent.eu.org%3A451%2Fannounce"
    "&tr=udp%3A%2F%2Fexodus.desync.com%3A6969%2Fannounce"
)

_FAIL_COUNT: int = 0
_FAIL_UNTIL: float = 0.0
_MAX_FAILS: int = 2
_FAIL_BACKOFF = 300.0  # 5 мин

# ── Shared session cache ──────────────────────────────────────────────────────
_COOKIES: dict[str, str] = {}
_SESSION_TIME: float = 0.0
_SESSION_TTL  = 3600 * 6   # re-login every 6 hours
_SESSION_LOCK = asyncio.Lock()


def _parse_size_mb(size_str: str) -> int:
    if not size_str:
        return 0
    s = size_str.replace(',', '.').strip()
    m = re.match(r'([\d.]+)\s*(ГБ|МБ|GB|MB)', s, re.I)
    if not m:
        return 0
    val, unit = float(m.group(1)), m.group(2).upper()
    return int(val * 1024 if unit in ('ГБ', 'GB') else val)


def _make_magnet(info_hash: str, title: str) -> str:
    dn = quote_plus(re.sub(r'[^\w\s-]', '', title).strip()[:80])
    return f"magnet:?xt=urn:btih:{info_hash.lower()}&dn={dn}{_TRACKERS}"


async def _login() -> dict[str, str]:
    """Авторизуемся на rutracker.org, возвращаем cookies."""
    global _FAIL_COUNT, _FAIL_UNTIL
    login    = os.getenv("RUTRACKER_LOGIN", "")
    password = os.getenv("RUTRACKER_PASSWORD", "")
    if not login or not password:
        logger.warning("[RuTracker] RUTRACKER_LOGIN / RUTRACKER_PASSWORD не заданы")
        return {}
    try:
        async with httpx.AsyncClient(
            headers=_HEADERS, follow_redirects=True, timeout=12
        ) as c:
            resp = await c.post(
                _LOGIN_URL,
                data={
                    "login_username": login,
                    "login_password": password,
                    "login":          "Вход",
                },
            )
        # Куки из финального ответа (follow_redirects=True)
        cookies = {k: v for k, v in resp.cookies.items()}
        if "bb_session" in cookies or "bb_data" in cookies:
            _FAIL_COUNT = 0
            _FAIL_UNTIL = 0.0
            logger.info(f"[RuTracker] login OK user={login}")
            return cookies
        logger.warning("[RuTracker] login failed (no cookie)")
        _FAIL_COUNT += 1
        if _FAIL_COUNT >= _MAX_FAILS:
            _FAIL_UNTIL = time.time() + _FAIL_BACKOFF
            logger.warning(f"[RuTracker] circuit breaker ON for 5 min")
        return {}
    except Exception as e:
        _FAIL_COUNT += 1
        if _FAIL_COUNT >= _MAX_FAILS:
            _FAIL_UNTIL = time.time() + _FAIL_BACKOFF
            logger.warning(f"[RuTracker] circuit breaker ON for 5 min")
        logger.error(f"[RuTracker] login error: {e}")
        return {}


async def _get_cookies() -> dict[str, str]:
    """Вернуть актуальные куки, при необходимости перелогиниться."""
    global _COOKIES, _SESSION_TIME
    if time.time() < _FAIL_UNTIL:
        return {}
    async with _SESSION_LOCK:
        if _COOKIES and (time.time() - _SESSION_TIME) < _SESSION_TTL:
            return _COOKIES
        _COOKIES      = await _login()
        _SESSION_TIME = time.time()
        return _COOKIES


async def _fetch_magnet_from_page(topic_id: str, cookies: dict) -> Optional[str]:
    """Зайти на страницу топика и вытащить magnet-ссылку."""
    url = f"{_BASE}/viewtopic.php?t={topic_id}"
    try:
        async with httpx.AsyncClient(
            headers=_HEADERS, cookies=cookies,
            follow_redirects=True, timeout=12,
        ) as c:
            r = await c.get(url)
        m = _MAGNET_RE.search(r.text)
        return m.group(0) if m else None
    except Exception as e:
        logger.debug(f"[RuTracker] page fetch {topic_id}: {e}")
        return None


class RuTrackerProvider(BaseProvider):
    name = "rutracker"
    source_type = "magnet"

    @property
    def enabled(self) -> bool:
        return bool(
            os.getenv("RUTRACKER_LOGIN") and os.getenv("RUTRACKER_PASSWORD")
        )

    async def search(self, query: str, category: str) -> list[SearchResult]:
        try:
            return await asyncio.wait_for(self._search_inner(query, category), timeout=30)
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"[RuTracker] search timeout/error: {e}")
            return []

    async def _search_inner(self, query: str, category: str) -> list[SearchResult]:
        cookies = await _get_cookies()
        if not cookies:
            return []

        # ── Поиск ──────────────────────────────────────────────────────────
        try:
            async with httpx.AsyncClient(
                headers=_HEADERS, cookies=cookies,
                follow_redirects=True, timeout=20,
            ) as c:
                r = await c.get(
                    _SEARCH_URL,
                    params={
                        "nm": query,
                        "o":  "10",   # сортировка по сидам (desc)
                        "s":  "2",
                    },
                )
        except Exception as e:
            logger.error(f"[RuTracker] search error: {e}")
            return []

        html = r.text

        # Список топиков из таблицы результатов
        # Строка: viewtopic.php?t=XXXXXX + название + сиды + размер
        rows = re.findall(
            r'viewtopic\.php\?t=(\d+)"[^>]*>([^<]{5,120})</a>'
            r'(?:.*?class="seedmed"[^>]*>(\d+)(?:.*?class="tor-size"[^>]*>([^<]+))?)?',
            html,
            re.DOTALL,
        )

        if not rows:
            # fallback: просто вытащим topic ids
            topic_ids = _TOPIC_RE.findall(html)[:10]
            if not topic_ids:
                logger.info(f"[RuTracker] нет результатов для '{query}'")
                return []
            rows = [(tid, f"RuTracker#{tid}", "0", "") for tid in topic_ids[:8]]

        # Сортируем по сидам
        def _seeds(row):
            try:
                return int(row[2])
            except Exception:
                return 0

        rows.sort(key=_seeds, reverse=True)
        top = rows[:8]

        # Параллельно получаем magnet-ссылки со страниц топиков
        async with httpx.AsyncClient(
            headers=_HEADERS, cookies=cookies,
            follow_redirects=True, timeout=12,
        ) as c:
            tasks = [
                c.get(f"{_BASE}/viewtopic.php?t={tid}")
                for tid, *_ in top
            ]
            responses = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[SearchResult] = []
        for (tid, raw_title, raw_seeds, raw_size), resp in zip(top, responses):
            if isinstance(resp, Exception):
                continue
            m = _MAGNET_RE.search(resp.text)
            if not m:
                continue
            magnet = m.group(0)
            info_hash = re.search(r'btih:([a-fA-F0-9]+)', magnet, re.I)
            if not info_hash:
                continue

            seeds    = int(raw_seeds) if raw_seeds.isdigit() else 0
            size_mb  = _parse_size_mb(raw_size)
            clean    = re.sub(r'<[^>]+>', '', raw_title).strip()
            meta     = f"  [{raw_size.strip()} 🌱{seeds}]" if seeds else ""
            label    = clean + meta
            validation = validate_torrent(clean, size_mb)

            results.append(SearchResult(
                id=info_hash.group(1).lower(),
                title=label,
                url=magnet,
                thumbnail=None,
                duration=None,
                channel="RuTracker",
                provider="torrent",
                source_type="magnet",
                source_title=clean,
                source_url=f"{_BASE}/viewtopic.php?t={tid}",
                extra={
                    "_validation":    validation,
                    "_is_suspicious": validation["risk_score"] > 0.5,
                    "seeders":        seeds,
                    "source":         "rutracker",
                },
            ))

        logger.info(f"[RuTracker] query='{query}' → {len(results)} magnets")
        return results

    async def get_stream(self, url: str):
        from .base import StreamInfo
        return StreamInfo(stream_url=url, provider=self.name, protocol="magnet")