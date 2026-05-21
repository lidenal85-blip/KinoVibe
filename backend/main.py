"""
main.py — KinoVibe API Server v5.0
FastAPI + Uvicorn + WebSocket signaling
Includes: platform filter, popularity filter, Watch Party invite links
"""

import logging
import uvicorn
from typing import Optional, Literal
import httpx
from fastapi import FastAPI, HTTPException, WebSocket, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from search import execute_search as search_videos, get_cache
import sys
sys.path.insert(0, "/opt/leviathan_engine")
from core.key_pool import get_pool
from signaling import get_signaling

from providers import REGISTRY, _by_name

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s >> %(name)s >> %(levelname)s >> %(message)s",
)
logger = logging.getLogger("kinovibe")

app = FastAPI(
    title="KinoVibe API",
    version="5.0.0",
    description="Мультипровайдерный AI агрегатор (YouTube, VK, Torrents, Kodik, HDRezka).",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def _invite_url(request: Request, code: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/room/{code}"


# ─── Модели ───────────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    category: str = "movies"
    platform: str = "all"      # "all" | "youtube" | "vk" | "torrent" | "kodik" | "hdrezka"
    popularity: str = "all"    # "all" | "rare" | "mid" | "mainstream"
    mode: str = "mood"         # "mood" | "search"


class CreateRoomRequest(BaseModel):
    movie_url: str = ""
    movie_title: str = ""


# ─── Эндпоинты ────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "5.0.0", "service": "KinoVibe Hub"}


@app.get("/pool/status")
async def pool_status():
    return get_pool().status()


@app.get("/cache/stats")
async def cache_stats():
    return get_cache().stats()


@app.post("/cache/clear")
async def cache_clear():
    await get_cache().clear()
    return {"ok": True, "message": "Cache cleared"}


@app.post("/search")
async def search(req: SearchRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    # Derive valid platforms dynamically from REGISTRY
    from providers import REGISTRY
    valid_platforms = {"all"} | {p.name for p in REGISTRY}
    if req.platform not in valid_platforms:
        raise HTTPException(status_code=400, detail=f"Invalid platform. Allowed: {sorted(valid_platforms)}")

    valid_popularity = {"all", "rare", "mid", "mainstream"}
    if req.popularity not in valid_popularity:
        raise HTTPException(status_code=400, detail=f"Invalid popularity. Allowed: {valid_popularity}")

    valid_modes = {"mood", "search"}
    if req.mode not in valid_modes:
        raise HTTPException(status_code=400, detail=f"Invalid mode. Allowed: {valid_modes}")

    logger.info(f"[SEARCH] query={req.query!r} category={req.category} platform={req.platform} popularity={req.popularity} mode={req.mode}")
    result = await search_videos(req.query, req.category, platform=req.platform, popularity=req.popularity, mode=req.mode)
    return result


@app.get("/stream")
async def get_stream_url(url: str, provider: Optional[str] = None):
    """Universal streaming endpoint — routes to the appropriate provider."""
    if not url:
        raise HTTPException(status_code=400, detail="url required")

    logger.info(f"[STREAM] Routing request for: {url[:60]} (provider suggestion: {provider})")

    # Magnet links are handled directly by WebTorrent on the frontend
    if url.startswith("magnet:"):
        return {
            "stream_url": url,
            "provider": "torrent",
            "protocol": "magnet",
            "source_type": "magnet",
        }

    chosen_provider = None
    if provider and provider in _by_name:
        chosen_provider = _by_name[provider]
    else:
        if "vk.com" in url or "vkvideo" in url:
            chosen_provider = _by_name.get("vk")
        elif "magnet:" in url or url.endswith(".torrent"):
            chosen_provider = _by_name.get("torrent")
        elif "kodik" in url:
            chosen_provider = _by_name.get("kodik")
        else:
            chosen_provider = _by_name.get("youtube")

    if not chosen_provider or not chosen_provider.enabled:
        raise HTTPException(status_code=400, detail=f"Provider '{provider}' is unavailable or disabled")

    try:
        stream_info = await chosen_provider.get_stream(url)
        if not stream_info:
            raise Exception("Empty stream info returned")
        return stream_info.to_dict()
    except Exception as e:
        logger.error(f"[STREAM ERROR] Provider {chosen_provider.name} failed: {e}")
        raise HTTPException(status_code=422, detail=f"Stream extraction failed: {str(e)}")


# ─── Watch Party / Rooms ──────────────────────────────────────────────────────

@app.post("/rooms/create")
async def create_room(req: CreateRoomRequest, request: Request):
    signaling = get_signaling()
    room_id = signaling.create_room(
        movie_url=req.movie_url,
        movie_title=req.movie_title,
    )
    invite_code = room_id
    return {
        "room_id": room_id,
        "invite_code": invite_code,
        "invite_url": _invite_url(request, invite_code),
    }


@app.get("/rooms")
async def list_rooms():
    return get_signaling().room_list()


@app.get("/rooms/join/{invite_code}")
async def join_room_info(invite_code: str, request: Request):
    """Returns room info for joining by invite code."""
    room = get_signaling().get_room(invite_code.upper())
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    return {
        "room_id": room.room_id,
        "invite_code": room.room_id,
        "invite_url": _invite_url(request, room.room_id),
        "movie_title": room.movie_title,
        "movie_url": room.movie_url,
        "peers": len(room.peers),
        "is_playing": room.is_playing,
        "position_sec": room.position_sec,
    }


@app.get("/rooms/{invite_code}")
async def get_room_by_invite(invite_code: str, request: Request):
    """Returns room data by invite code."""
    room = get_signaling().get_room(invite_code.upper())
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    return {
        "room_id": room.room_id,
        "invite_code": room.room_id,
        "invite_url": _invite_url(request, room.room_id),
        "movie_title": room.movie_title,
        "movie_url": room.movie_url,
        "peers": len(room.peers),
        "is_playing": room.is_playing,
        "position_sec": room.position_sec,
    }


@app.get("/image-proxy")
async def image_proxy(url: str):
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        return Response(
            content=resp.content,
            media_type=resp.headers.get("content-type", "image/jpeg"),
        )
    except Exception:
        raise HTTPException(status_code=502, detail="Failed to fetch image")


@app.websocket("/ws/{peer_id}")
async def websocket_endpoint(ws: WebSocket, peer_id: str):
    await get_signaling().handle(ws, peer_id)


# ─── Запуск ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8110,
        reload=False,
        log_level="info",
    )
