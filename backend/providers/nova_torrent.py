"""
nova_torrent.py — Torrent search via TorAPI (github.com/Lifailon/TorAPI)
Public free API on Vercel: torapi.vercel.app
Providers: RuTor (rutor.info), Kinozal, NoNameClub, RuTracker

No scraping, no auth, no keys — pure REST API calls.
Returns magnet links built from Hash field.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import httpx

from .base import BaseProvider, SearchResult
from .torrent_validator import validate_torrent

logger = logging.getLogger("kinovibe.providers.torapi")

_BASE = "https://torapi.vercel.app/api"

_TRACKERS = (
    "&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce"
    "&tr=udp%3A%2F%2Fopen.demonii.com%3A1337%2Fannounce"
    "&tr=udp%3A%2F%2Ftracker.torrent.eu.org%3A451%2Fannounce"
    "&tr=udp%3A%2F%2Fexodus.desync.com%3A6969%2Fannounce"
)

# Providers to query — RuTracker often needs login, skip it for search
_PROVIDERS = ["rutor", "nonameclub", "kinozal", "rutracker"]

_HEADERS = {"User-Agent": "KinoVibe/5.0 TorAPI-Client"}


def _make_magnet(torrent_hash: str, title: str) -> str:
    name = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '+')[:80]
    return f"magnet:?xt=urn:btih:{torrent_hash}&dn={name}{_TRACKERS}"


def _torrent_url_result(item: dict, provider_label: str) -> Optional[SearchResult]:
    """Kinozal/RuTracker via TorAPI: no Hash, but has Torrent URL.
    We pass the .torrent URL to TorrServer via source_url so the stream layer
    can add it to TorrServer and get a hash.
    """
    torrent_url = item.get('Torrent', '')
    if not torrent_url:
        return None
    raw_name = item.get('Name', '') or item.get('Title', '')
    seeds = int(item.get('Seeds', 0) or 0)
    size = item.get('Size', '').replace('\xa0', ' ')
    date = item.get('Date', '')
    item_id = str(item.get('Id', ''))
    page_url = item.get('Url', '')

    meta_parts = []
    if size:
        meta_parts.append(size)
    if seeds > 0:
        meta_parts.append(f'🌱{seeds}')
    meta = ' · '.join(meta_parts)
    label = raw_name + (f'  [{meta}]' if meta else '')

    size_mb = _parse_size_mb(size) or 0
    validation = validate_torrent(raw_name, size_mb)

    return SearchResult(
        id=item_id,
        title=label,
        url=torrent_url,        # .torrent file URL — TorrServer может добавить по URL
        thumbnail=item.get('Poster') or None,
        duration=None,
        channel=provider_label,
        provider="torrent",
        source_type="torrent_url",  # отличаем от magnet
        description=item.get('Description', ''),
        source_title=raw_name,
        source_url=page_url,
        extra={
            "torrent_file_url": torrent_url,
            "_seeds": seeds,
            "_validation": validation,
            "_is_suspicious": validation["risk_score"] > 0.5,
        },
    )


def _parse_size_mb(size_str: str) -> Optional[int]:
    """Convert '2.26 GB' or '450 MB' to MB."""
    if not size_str:
        return None
    s = size_str.replace('\xa0', ' ').replace(',', '.').strip()
    m = re.match(r'([\d.]+)\s*(GB|MB|KB|TB)', s, re.I)
    if not m:
        return None
    val, unit = float(m.group(1)), m.group(2).upper()
    return int(val * {'TB': 1024*1024, 'GB': 1024, 'MB': 1, 'KB': 0.001}[unit])


def _result_from_item(item: dict, provider_label: str) -> Optional[SearchResult]:
    torrent_hash = item.get('Hash', '')
    if not torrent_hash:
        return None

    raw_name = item.get('Name', '') or item.get('Title', '')
    seeds    = int(item.get('Seeds', 0) or 0)
    size     = item.get('Size', '').replace('\xa0', ' ')
    date     = item.get('Date', '')
    item_id  = item.get('Id', torrent_hash[:8])
    page_url = item.get('Url', '')  # direct link to tracker page

    magnet = _make_magnet(torrent_hash, raw_name)

    # Clean label: size + seeds indicator
    meta_parts = []
    if size:
        meta_parts.append(size)
    if seeds > 0:
        meta_parts.append(f'🌱{seeds}')
    meta = ' · '.join(meta_parts)

    # title field: use raw torrent name as-is (TMDB layer will add clean title)
    label = raw_name
    if meta:
        label += f'  [{meta}]'

    size_mb = _parse_size_mb(size) or 0
    validation = validate_torrent(raw_name, size_mb)

    return SearchResult(
        id=str(item_id),
        title=label,
        url=magnet,
        thumbnail=item.get('Poster') or None,
        duration=None,
        channel=f"{provider_label}",
        provider="torrent",
        source_type="magnet",
        description=item.get('Description', ''),
        source_title=raw_name,
        source_url=page_url,
        extra={
            "_validation": validation,
            "_is_suspicious": validation["risk_score"] > 0.5,
        },
    )


async def _search_provider(
    provider: str,
    query: str,
    client: httpx.AsyncClient,
    max_results: int = 8,
) -> list[SearchResult]:
    try:
        r = await client.get(
            f"{_BASE}/search/title/{provider}",
            params={"query": query},
            headers=_HEADERS,
            timeout=12,
        )
        if r.status_code != 200:
            logger.debug(f"[TorAPI] {provider} → HTTP {r.status_code}")
            return []
        data = r.json()
    except Exception as e:
        logger.warning(f"[TorAPI] {provider} failed: {e}")
        return []

    # Response is list of items OR dict with 'Result'/'Error'
    if isinstance(data, dict):
        if 'Result' in data or 'Error' in data:
            return []
        data = list(data.values())[0] if data else []
    if not isinstance(data, list):
        return []

    results = []
    provider_label = {'rutor': 'RuTor', 'kinozal': 'Kinozal',
                      'nonameclub': 'NNMClub', 'rutracker': 'RuTracker'}.get(provider, provider)

    # Sort: browser-friendly first (MKV/1080p), AVI last
    def _sort_key(item):
        name = item.get('Name', '').lower()
        seeds = int(item.get('Seeds', 0) or 0)
        # Detect likely format from title keywords
        if any(k in name for k in ('hevc', 'x265', 'h.265', 'h265', 'web-dl', 'bluray', 'bdrip', 'bdremux')):
            fmt = 0  # almost certainly MKV
        elif '1080p' in name or '720p' in name:
            fmt = 1  # modern encode, likely MKV
        elif 'webrip-avc' in name or 'bdrip-avc' in name or 'dvdrip' in name or 'от files' in name or 'от generalfilm' in name:
            fmt = 4  # likely AVI (legacy Russian AVC rips)
        elif 'webrip' in name or 'web-dlrip' in name:
            fmt = 2  # could be either
        else:
            fmt = 3
        return (fmt, -seeds)

    data.sort(key=_sort_key)

    for item in data[:max_results]:
        if item.get('Hash'):
            r = _result_from_item(item, provider_label)
        else:
            r = _torrent_url_result(item, provider_label)
        if r:
            results.append(r)

    logger.info(f"[TorAPI] {provider} query={query!r} → {len(results)} results")
    return results


class NovaTorrentProvider(BaseProvider):
    name = "torrent"
    source_type = "magnet"

    async def search(self, query: str, category: str, max_results: int = 10) -> list[SearchResult]:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            tasks = [
                _search_provider(p, query, client, max_results)
                for p in _PROVIDERS
            ]
            per_provider = await asyncio.gather(*tasks, return_exceptions=True)

        combined: list[SearchResult] = []
        for batch in per_provider:
            if isinstance(batch, list):
                combined.extend(batch)

        # Deduplicate by hash (different providers may return same torrent)
        seen: set[str] = set()
        unique: list[SearchResult] = []
        for r in combined:
            h = re.search(r'btih:([a-fA-F0-9]+)', r.url or '')
            if h:
                key = h.group(1).lower()
            elif r.url:
                # torrent_url: deduplicate by URL
                key = r.url.split('?')[0]
            else:
                key = r.title or str(id(r))
            if key not in seen:
                seen.add(key)
                unique.append(r)

        # Sort by seeds (desc)
        unique.sort(
            key=lambda r: int(re.search(r'🌱(\d+)', r.title or '').group(1))
                          if re.search(r'🌱(\d+)', r.title or '') else 0,
            reverse=True,
        )
        return unique[:max_results]

    async def get_stream(self, url: str):
        from .base import StreamInfo
        return StreamInfo(stream_url=url, provider=self.name, protocol="magnet")
