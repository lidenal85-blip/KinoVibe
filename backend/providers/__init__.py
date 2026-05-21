"""
providers/__init__.py — Provider registry and aggregate search

REGISTRY order determines result ordering in the combined response.
Disabled providers are skipped silently; their search() is never called.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Optional

from .base import BaseProvider, SearchResult, StreamInfo
from .youtube import YouTubeProvider
from .vk import VKProvider
from .kodik import KodikProvider
from .hdrezka import HDRezkaProvider
from .rutube import RutubeProvider
from .filmix import FilmixProvider
from .torrent import TorrentProvider

logger = logging.getLogger("kinovibe.providers")

# Ordered list of all providers — YouTube first as primary source
REGISTRY: list[BaseProvider] = [
    YouTubeProvider(),
    VKProvider(),
    KodikProvider(),
    HDRezkaProvider(),
    RutubeProvider(),
    FilmixProvider(),
    TorrentProvider(),
]

_by_name: dict[str, BaseProvider] = {p.name: p for p in REGISTRY}


def get_provider(name: str) -> Optional[BaseProvider]:
    return _by_name.get(name)


async def aggregate_search(query: str, category: str, platform: str = "all") -> list[SearchResult]:
    """Fan out search to enabled providers, optionally filtered by platform name."""
    if platform and platform != "all":
        enabled = [p for p in REGISTRY if p.enabled and p.name == platform]
    else:
        enabled = [p for p in REGISTRY if p.enabled]

    if not enabled:
        logger.warning(f"No enabled providers for platform='{platform}'")
        return []

    tasks = [p.search(query, category) for p in enabled]
    gathered = await asyncio.gather(*tasks, return_exceptions=True)

    results: list[SearchResult] = []
    for provider, outcome in zip(enabled, gathered):
        if isinstance(outcome, Exception):
            logger.warning(f"Provider '{provider.name}' search error: {outcome}")
        elif isinstance(outcome, list):
            results.extend(outcome)

    return results


async def get_stream(url: str, provider_name: str = "youtube") -> StreamInfo:
    """Route stream extraction to the named provider."""
    provider = get_provider(provider_name)
    if not provider:
        raise ValueError(f"Unknown provider: '{provider_name}'. "
                         f"Available: {list(_by_name)}")
    return await provider.get_stream(url)


__all__ = [
    "BaseProvider", "SearchResult", "StreamInfo",
    "YouTubeProvider", "VKProvider", "KodikProvider",
    "HDRezkaProvider", "RutubeProvider", "FilmixProvider", "TorrentProvider",
    "REGISTRY", "get_provider", "aggregate_search", "get_stream",
]
