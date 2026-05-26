"""
input_router.py — Deterministic input classifier
Routes user input to the correct handler without LLM guessing.

Priority:
  1. URL → direct_play
  2. Magnet/torrent → torrent_stream
  3. Search command (!torrent, :t, /torrent) → torrent_search
  4. File ID (Telegram) → cache_lookup
  5. Default → standard_search
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Literal

RouteType = Literal[
    "direct_play",      # known playable URL
    "torrent_search",   # explicit torrent search
    "torrent_stream",   # magnet / .torrent URL
    "cache_lookup",     # Telegram file_id / known hash
    "standard_search",  # default AI-powered search
]

_RE_URL     = re.compile(r'^https?://', re.I)
_RE_MAGNET  = re.compile(r'^magnet:\?xt=', re.I)
_RE_TORRENT = re.compile(r'\.torrent$', re.I)
_RE_FILEID  = re.compile(r'^[A-Za-z0-9_-]{20,}$')   # Telegram file_id pattern

# Explicit torrent search prefixes
_TORRENT_PREFIXES = ("!torrent ", ":t ", "/torrent ", "torrent:", "t:")

# URLs that can be played directly (no extra extraction)
_DIRECT_HOSTS = {
    "youtube.com", "youtu.be",
    "vk.com", "vkvideo.ru",
    "rutube.ru",
    "ok.ru",
    "t.me",                   # Telegram media links
}


@dataclass
class RouteResult:
    route:   RouteType
    payload: str            # cleaned query/url
    meta:    dict           # extra context


def classify(raw: str) -> RouteResult:
    q = raw.strip()

    # 1. Magnet → torrent stream
    if _RE_MAGNET.match(q):
        return RouteResult("torrent_stream", q, {"protocol": "magnet"})

    # 2. .torrent URL
    if _RE_TORRENT.search(q) and _RE_URL.match(q):
        return RouteResult("torrent_stream", q, {"protocol": "torrent_url"})

    # 3. Explicit torrent search prefix
    for prefix in _TORRENT_PREFIXES:
        if q.lower().startswith(prefix):
            payload = q[len(prefix):].strip()
            return RouteResult("torrent_search", payload, {"original_prefix": prefix})

    # 4. Direct playable URL
    if _RE_URL.match(q):
        host = _extract_host(q)
        if any(host.endswith(d) for d in _DIRECT_HOSTS):
            return RouteResult("direct_play", q, {"host": host})
        # Unknown URL — still try direct play
        return RouteResult("direct_play", q, {"host": host, "unknown_host": True})

    # 5. Telegram file_id (long alphanumeric, no spaces)
    if " " not in q and _RE_FILEID.match(q):
        return RouteResult("cache_lookup", q, {})

    # 6. Default: standard search
    return RouteResult("standard_search", q, {})


def _extract_host(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


# ── FastAPI endpoint helper ────────────────────────────────────────────────────

def route_to_dict(r: RouteResult) -> dict:
    return {"route": r.route, "payload": r.payload, "meta": r.meta}
