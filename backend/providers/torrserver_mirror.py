"""
torrserver_mirror.py — Выбор лучшей ноды TorrServer по пингу + prefetch первых байт.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

logger = logging.getLogger("kinovibe.torrserver_mirror")

TORRSERVER_NODES: list[str] = [
    "http://127.0.0.1:8099",    # Локальная нода (приоритет)
]

_PING_CACHE: dict[str, float] = {}   # node_url → latency_ms
_PING_CACHE_TS: float = 0.0
_PING_TTL: float = 300.0             # 5 минут


async def select_best_node() -> str:
    """Вернуть адрес TorrServer с наименьшим пингом (с кэшом 5 мин)."""
    global _PING_CACHE, _PING_CACHE_TS

    if time.time() - _PING_CACHE_TS < _PING_TTL and _PING_CACHE:
        best = min(_PING_CACHE, key=_PING_CACHE.get)
        logger.debug(f"[TS_MIRROR] Cached best: {best} ({_PING_CACHE[best]:.0f}ms)")
        return best

    latencies: dict[str, float] = {}

    async def ping(node: str) -> None:
        try:
            t0 = time.time()
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.get(f"{node}/stat")
            if r.status_code == 200:
                latencies[node] = (time.time() - t0) * 1000
                logger.debug(f"[TS_MIRROR] {node}: {latencies[node]:.0f}ms")
        except Exception as exc:
            logger.debug(f"[TS_MIRROR] {node} unreachable: {exc}")

    await asyncio.gather(*[ping(n) for n in TORRSERVER_NODES])

    if not latencies:
        logger.warning("[TS_MIRROR] No responsive TorrServer nodes found, using default")
        return TORRSERVER_NODES[0]

    best = min(latencies, key=latencies.get)
    _PING_CACHE = latencies
    _PING_CACHE_TS = time.time()
    logger.info(f"[TS_MIRROR] Best node: {best} ({latencies[best]:.0f}ms)")
    return best


async def prefetch_torrent(magnet: str, torrent_hash: str, max_mb: int = 10) -> None:
    """
    Запустить prefetch первых байт торрента на TorrServer (фоновая задача).
    Помогает ускорить старт стриминга при последующем воспроизведении.
    """
    node = await select_best_node()

    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            # Добавить торрент (если не добавлен)
            await c.post(
                f"{node}/torrents",
                json={"action": "add", "link": magnet, "title": "prefetch", "save_to_db": False},
            )
            await asyncio.sleep(2)

            # Получить список файлов
            lst = await c.post(f"{node}/torrents", json={"action": "list"})
            if lst.status_code != 200:
                return

            torrents = lst.json() if isinstance(lst.json(), list) else []
            files: list[dict] = []
            for t in torrents:
                if t.get("hash", "").lower() == torrent_hash.lower():
                    import json as _json
                    raw = t.get("data", "")
                    if isinstance(raw, str) and raw:
                        try:
                            inner = _json.loads(raw)
                            files = inner.get("TorrServer", {}).get("Files", [])
                        except Exception:
                            pass
                    if not files:
                        files = t.get("file_stats", [])
                    break

            if not files:
                return

            VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm"}
            video_files = [
                f for f in files
                if any((f.get("path") or f.get("Name", "")).lower().endswith(e) for e in VIDEO_EXTS)
            ]
            if not video_files:
                return

            best_file = max(video_files, key=lambda f: f.get("length", f.get("Length", 0)))
            file_size = best_file.get("length", best_file.get("Length", 0))
            file_id = best_file.get("id", best_file.get("Id", 0))

            if file_size <= 0:
                return

            prefetch_bytes = min(max_mb * 1_048_576, max(1_048_576, file_size // 50))
            stream_url = (
                f"http://78.17.24.96:8081/torrserver/stream"
                f"?link={torrent_hash}&index={file_id}&play"
            )

            r = await c.get(
                stream_url,
                headers={"Range": f"bytes=0-{prefetch_bytes}"},
                timeout=15,
            )
            if r.status_code in (200, 206):
                logger.info(
                    f"[TS_MIRROR] Prefetch OK: {torrent_hash[:12]}... "
                    f"{len(r.content) // 1024}KB cached"
                )

    except Exception as exc:
        logger.debug(f"[TS_MIRROR] Prefetch error for {torrent_hash[:12]}: {exc}")
