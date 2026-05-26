"""
torrent.py — Torrent search + TorrServer streaming bridge

Search strategy (in order):
  1. 1337x — parses https://www.1337x.to/search/{query}/1/ HTML for name/seeds,
     then fetches detail pages concurrently to extract magnet links.
  2. apibay (Pirate Bay API) — JSON API at https://apibay.org/q.php, used when
     1337x is unavailable (returns 403 from this host or times out).
  3. Jackett — legacy XML API (requires JACKETT_API_KEY env var).

Stream via TorrServer (https://github.com/YouROK/TorrServer):
    docker run -d --name torrserver -p 8090:8090 yourok/torrserver

Environment variables:
    TORRSERVER_URL   — base URL of TorrServer (default: http://localhost:8090)
    JACKETT_URL      — Jackett indexer proxy URL (default: http://localhost:9117)
    JACKETT_API_KEY  — Jackett API key
"""

from __future__ import annotations
import asyncio
import logging
import os
import re
from urllib.parse import quote
from urllib.parse import quote_plus
import httpx
from .base import BaseProvider, SearchResult, StreamInfo

logger = logging.getLogger("kinovibe.providers.torrent")

TORRSERVER_URL = os.environ.get("TORRSERVER_URL", "http://localhost:8090")
JACKETT_URL = os.environ.get("JACKETT_URL", "http://localhost:9117")
JACKETT_KEY = os.environ.get("JACKETT_API_KEY", "")

_1337X_BASE = "https://1337x.to"
_APIBAY_BASE = "https://apibay.org"
_KINOZAL_BASE = "https://kinozal.tv"
_KZ_ID_RE = re.compile(r'/details\.php\?id=(\d+)')

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) "
        "Gecko/20100101 Firefox/125.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Default announce trackers for magnet links built from info_hash
_TRACKERS = (
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "https://tracker.gbitt.info/announce",
)

_JACKETT_CATEGORIES = {
    "movies": ["2000"],
    "series": ["5000"],
    "anime": ["5070"],
    "shorts": ["2000"],
}

# 1337x regex patterns
_ROW_RE = re.compile(
    r'<td class="name">\s*<[^>]+>[^<]*</[^>]+>\s*'
    r'<a\s+href="(/torrent/[^"]+)"[^>]*>([^<]+)</a>',
    re.DOTALL,
)
_SEEDS_RE = re.compile(r'<td class="seeds">(\d+)</td>')
_MAGNET_RE = re.compile(r'href="(magnet:\?[^"]+)"')


def _build_magnet(info_hash: str, name: str) -> str:
    trackers = "&".join(f"tr={quote(t)}" for t in _TRACKERS)
    return f"magnet:?xt=urn:btih:{info_hash}&dn={quote_plus(name)}&{trackers}"


class TorrentProvider(BaseProvider):
    name = "torrent"
    source_type = "magnet"

    @property
    def enabled(self) -> bool:
        return True

    # ── TorrServer health ────────────────────────────────────────────────────

    async def _torrserver_alive(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(f"{TORRSERVER_URL}/echo")
                return r.status_code == 200
        except Exception:
            return False

    # ── Search: 1337x ────────────────────────────────────────────────────────

    async def _search_1337x(self, query: str) -> list[SearchResult]:
        slug = quote_plus(query)
        search_url = f"{_1337X_BASE}/search/{slug}/1/"
        try:
            async with httpx.AsyncClient(
                timeout=15, headers=_HEADERS, follow_redirects=True
            ) as c:
                r = await c.get(search_url)

            if r.status_code != 200:
                logger.warning(f"1337x returned HTTP {r.status_code} for {search_url}")
                return []

            html = r.text
            rows = _ROW_RE.findall(html)
            seeds_all = _SEEDS_RE.findall(html)

            if not rows:
                logger.debug("1337x: no rows parsed from HTML")
                return []

            # Pair up rows with seed counts
            candidates = []
            for i, (detail_path, title) in enumerate(rows[:8]):
                seeds = int(seeds_all[i]) if i < len(seeds_all) else 0
                candidates.append((title.strip(), detail_path, seeds))

            # Sort by seeds descending, take top 5
            candidates.sort(key=lambda x: x[2], reverse=True)
            top = candidates[:5]

            # Fetch detail pages concurrently to get magnet links
            base = _1337X_BASE
            async with httpx.AsyncClient(
                timeout=10, headers=_HEADERS, follow_redirects=True
            ) as c:
                tasks = [c.get(f"{base}{path}") for _, path, _ in top]
                responses = await asyncio.gather(*tasks, return_exceptions=True)

            results = []
            for (title, _, seeds), resp in zip(top, responses):
                if isinstance(resp, Exception):
                    continue
                m = _MAGNET_RE.search(resp.text)
                if not m:
                    continue
                magnet = m.group(1)
                results.append(SearchResult(
                    id=magnet[:60],
                    title=title,
                    url=magnet,
                    provider=self.name,
                    source_type=self.source_type,
                    extra={"seeders": seeds, "source": "1337x"},
                ))

            logger.info(f"1337x: found {len(results)} magnets for '{query}'")
            return results

        except Exception as e:
            logger.error(f"1337x search failed: {e}")
            return []

    # ── Search: apibay (Pirate Bay API) ─────────────────────────────────────

    async def _apibay_query(self, query: str, cat_code: str) -> list[SearchResult]:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(
                    f"{_APIBAY_BASE}/q.php",
                    params={"q": query, "cat": cat_code},
                )
            if r.status_code != 200:
                logger.warning(f"apibay returned HTTP {r.status_code}")
                return []
            items = r.json()
            if not items or (len(items) == 1 and items[0].get("name") == "No results returned"):
                return []
            results = []
            for item in items[:10]:
                info_hash = item.get("info_hash", "")
                name = item.get("name", "")
                if not info_hash or not name:
                    continue
                seeders = int(item.get("seeders", 0))
                magnet = _build_magnet(info_hash, name)
                results.append(SearchResult(
                    id=info_hash,
                    title=name,
                    url=magnet,
                    provider=self.name,
                    source_type=self.source_type,
                    extra={
                        "seeders": seeders,
                        "size_bytes": int(item.get("size", 0)),
                        "source": "apibay",
                    },
                ))
            return results
        except Exception as e:
            logger.error(f"apibay query cat={cat_code} failed: {e}")
            return []

    async def _search_apibay(self, query: str, category: str) -> list[SearchResult]:
        # apibay category codes: 200=Video, 207=HD Movies, 201=Movies, 205=TV
        # Try HD first (207), fallback to generic Video (200)
        if category in ("movies", "shorts"):
            primary_cat, fallback_cat = "207", "200"
        elif category == "series":
            primary_cat, fallback_cat = "205", "200"
        else:
            primary_cat, fallback_cat = "200", None

        # Add "rus" suffix to improve Russian content discovery
        ru_query = f"{query} rus"
        results = await self._apibay_query(ru_query, primary_cat)
        if not results:
            # Try without russian suffix
            results = await self._apibay_query(query, primary_cat)
        if not results and fallback_cat:
            results = await self._apibay_query(query, fallback_cat)

        logger.info(f"apibay: found {len(results)} results for '{query}'")
        return results

    # ── Search: Jackett (legacy) ─────────────────────────────────────────────

    async def _search_jackett(self, query: str, category: str) -> list[SearchResult]:
        if not JACKETT_KEY:
            return []
        cats = _JACKETT_CATEGORIES.get(category, ["2000"])
        try:
            rus_query = f"rus dubbed {query}"
            params: dict = {"apikey": JACKETT_KEY, "Query": rus_query}
            for cat in cats:
                params.setdefault("Category[]", []).append(cat)  # type: ignore[attr-defined]

            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(
                    f"{JACKETT_URL}/api/v2.0/indexers/all/results",
                    params=params,
                )
            data = r.json()
            results = []
            for item in data.get("Results", [])[:10]:
                magnet = item.get("MagnetUri") or item.get("Link", "")
                if not magnet:
                    continue
                results.append(SearchResult(
                    id=item.get("InfoHash", "") or magnet[:40],
                    title=item.get("Title", ""),
                    url=magnet,
                    thumbnail=item.get("Poster"),
                    provider=self.name,
                    source_type=self.source_type,
                    extra={
                        "seeders": item.get("Seeders", 0),
                        "size_bytes": item.get("Size", 0),
                        "source": "jackett",
                    },
                ))
            return results
        except Exception as e:
            logger.error(f"Jackett search failed: {e}")
            return []

    # ── Search: Kinozal + apibay cross-reference ─────────────────────────────

    async def _search_kinozal_hybrid(self, query: str, category: str) -> list[SearchResult]:
        """Search kinozal.tv (best RU coverage), extract English titles, cross-reference apibay."""
        slug = quote_plus(query)
        search_url = f"{_KINOZAL_BASE}/browse.php?s={slug}&g=0&c=0&v=0&d=0&w=0&t=0&f=0"
        try:
            async with httpx.AsyncClient(timeout=15, headers=_HEADERS, follow_redirects=True) as c:
                r = await c.get(search_url)
            if r.status_code != 200:
                return []

            html = r.text
            detail_ids = _KZ_ID_RE.findall(html)

            parsed = []
            for row_html in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL):
                cells_raw = re.findall(r'<td[^>]*>(.*?)</td>', row_html, re.DOTALL)
                if len(cells_raw) < 4:
                    continue
                title_cell = re.sub(r'<[^>]+>', '', cells_raw[1]).strip()
                if not title_cell or len(title_cell) < 5:
                    continue
                title_clean = re.sub(r'\s*\d+$', '', title_cell).strip()
                seeds_str = re.sub(r'[^\d]', '', cells_raw[3]) or '0'
                size_str = re.sub(r'<[^>]+>', '', cells_raw[2]).strip()
                parsed.append({'title_full': title_clean, 'seeds': int(seeds_str), 'size': size_str})

            if not parsed:
                return []

            parsed.sort(key=lambda x: x['seeds'], reverse=True)
            top = parsed[:6]

            def extract_en_year(title: str):
                parts = [p.strip() for p in title.split('/')]
                year_m = re.search(r'\b(19|20)\d{2}\b', title)
                year = year_m.group(0) if year_m else ''
                en = parts[1] if len(parts) >= 2 else parts[0]
                en = re.sub(r'\b(19|20)\d{2}\b.*$', '', en).strip()
                return en, year

            results = []
            for i, item in enumerate(top):
                en_title, year = extract_en_year(item['title_full'])
                apibay_q = f"{en_title} {year}".strip()
                magnet_results = await self._search_apibay(apibay_q, category)
                ru_title = item['title_full'].split('/')[0].strip()
                if magnet_results:
                    best = magnet_results[0]
                    results.append(SearchResult(
                        id=best.id,
                        title=ru_title or best.title,
                        url=best.url,
                        provider=self.name,
                        source_type=self.source_type,
                        extra={**best.extra, 'size': item['size'], 'source': 'kinozal+apibay'},
                    ))
                else:
                    detail_id = detail_ids[i] if i < len(detail_ids) else None
                    kz_url = (f"{_KINOZAL_BASE}/details.php?id={detail_id}"
                              if detail_id else search_url)
                    results.append(SearchResult(
                        id=detail_id or ru_title[:20],
                        title=ru_title,
                        url=kz_url,
                        provider=self.name,
                        source_type="site",
                        extra={'seeds': item['seeds'], 'size': item['size'], 'source': 'kinozal'},
                    ))

            logger.info(f"kinozal hybrid: {len(results)} results for '{query}'")
            return results
        except Exception as e:
            logger.error(f"kinozal hybrid search failed: {e}")
            return []

    # ── Main search ──────────────────────────────────────────────────────────

    async def search(self, query: str, category: str) -> list[SearchResult]:
        # Primary: kinozal (best RU coverage) + apibay for magnets
        results = await self._search_kinozal_hybrid(query, category)
        if results:
            return results

        # Fallback: 1337x
        results = await self._search_1337x(query)
        if results:
            return results

        # Fallback: apibay direct
        results = await self._search_apibay(query, category)
        if results:
            return results

        # Legacy Jackett
        return await self._search_jackett(query, category)

    # ── Streaming via TorrServer ─────────────────────────────────────────────

    async def get_stream(self, url: str) -> StreamInfo:
        if url.startswith("magnet:"):
            raise ValueError(
                "Для воспроизведения магнет-ссылок используется WebTorrent плеер. TorrServer не запущен."
            )

        if not await self._torrserver_alive():
            raise RuntimeError(
                "TorrServer is not running. "
                f"Start it with: docker run -d --name torrserver -p 8090:8090 yourok/torrserver  "
                f"(TORRSERVER_URL={TORRSERVER_URL})"
            )

        async with httpx.AsyncClient(timeout=30) as c:
            add_resp = await c.post(
                f"{TORRSERVER_URL}/torrents/add",
                json={"link": url, "save_to_db": False},
                headers={"Content-Type": "application/json"},
            )
            if add_resp.status_code not in (200, 201):
                raise ValueError(
                    f"TorrServer /torrents/add returned {add_resp.status_code}: {add_resp.text[:200]}"
                )

            torrent = add_resp.json()
            info_hash: str = torrent.get("hash", "")
            file_stats: list = torrent.get("file_stats") or []

            if file_stats:
                best = max(file_stats, key=lambda f: f.get("length", 0))
                file_index: int = best.get("id", 0)
                filename: str = best.get("path", "video") or "video"
            else:
                file_index = 0
                filename = "video"

        stream_url = (
            f"{TORRSERVER_URL}/stream/{info_hash}/{file_index}/{quote(filename)}"
        )
        logger.info(f"[TORRENT] stream ready: {stream_url[:80]}")
        return StreamInfo(
            stream_url=stream_url,
            provider=self.name,
            protocol="http",
            extra={"hash": info_hash, "file_index": file_index, "filename": filename},
        )
