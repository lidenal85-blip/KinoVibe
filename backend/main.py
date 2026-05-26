"""
main.py — KinoVibe API Server v5.0
FastAPI + Uvicorn + WebSocket signaling
Includes: platform filter, popularity filter, Watch Party invite links
"""

import asyncio
import logging
import os
import shutil
import time
import uuid
import uvicorn
from pathlib import Path
from typing import Optional, Literal
import httpx
from fastapi import FastAPI, HTTPException, WebSocket, Request, Response, Cookie
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from search import execute_search as search_videos, get_cache, get_recommendations, find_film_streams
from providers.tmdb_client import get_tv_seasons, get_tv_season_episodes
from input_router import classify as route_input, route_to_dict
from subtitle import (
    start_job as subtitle_start_job,
    get_job as subtitle_get_job,
    init_model_pool,
    cleanup_loop as subtitle_cleanup_loop,
    JOBS_BASE as SUBTITLE_JOBS_BASE,
)
import sys
sys.path.insert(0, "/opt/leviathan_engine")
from core.key_pool import get_pool
from signaling import get_signaling

from providers import REGISTRY, _by_name
from vk_auth import router as vk_auth_router, get_user_token

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s >> %(name)s >> %(levelname)s >> %(message)s",
)
logger = logging.getLogger("kinovibe")

# ─── HLS constants ────────────────────────────────────────────────────────────
_STREAM_DIR = Path("/tmp/kv_streams")
_STREAM_DIR.mkdir(parents=True, exist_ok=True)
_YTDLP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "venv", "bin", "yt-dlp")
_active_streams: dict[str, dict] = {}

app = FastAPI(
    title="KinoVibe API",
    version="5.0.0",
    description="Мультипровайдерный AI агрегатор (YouTube, VK, Torrents, Kodik, HDRezka).",
)
app.include_router(vk_auth_router)

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


class RecommendRequest(BaseModel):
    query: str
    category: str = "movies"


class FilmStreamsRequest(BaseModel):
    title: str
    year: Optional[int] = None
    category: str = "movies"


class SubtitleStartRequest(BaseModel):
    url: str
    mode: str = "subtitles"       # "subtitles" | "voiceover" | "original"
    source_lang: Optional[str] = None   # None = autodetect
    target_lang: str = "ru"


class HLSStartRequest(BaseModel):
    url: str
    stream_id: str = ""
    start_sec: int = 0   # seek offset — generate segments from this position


class CreateRoomRequest(BaseModel):
    movie_url: str = ""
    movie_title: str = ""


# ─── HLS pipeline ────────────────────────────────────────────────────────────

async def _ytdlp_get_duration(url: str) -> int:
    """Fast: get video duration (seconds) via yt-dlp --print duration."""
    try:
        proc = await asyncio.create_subprocess_exec(
            _YTDLP, "--print", "duration", "--no-download", "--no-playlist",
            "--extractor-args", "youtube:player_client=android",
            url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
        return int(float(stdout.decode().strip()))
    except Exception:
        return 0


async def _ffmpeg_encode_hls(stream_id: str, video_url: str, audio_url: str | None,
                              stream_dir: Path, start_sec: int) -> None:
    """Run ffmpeg to produce HLS segments. Uses cached direct URL — no yt-dlp."""
    state = _active_streams.get(stream_id, {})
    try:
        ffmpeg_cmd = ["ffmpeg", "-y"]
        if start_sec > 0:
            ffmpeg_cmd += ["-ss", str(start_sec)]
        if audio_url:
            ffmpeg_cmd += ["-i", video_url, "-i", audio_url]
        else:
            ffmpeg_cmd += ["-i", video_url]
        ffmpeg_cmd += [
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-c:a", "aac", "-b:a", "128k",
            "-vf", "scale=-2:480",
            "-hls_time", "3",           # 3s segments: faster first playback
            "-hls_list_size", "0",
            "-hls_flags", "append_list",
            "-hls_segment_filename", str(stream_dir / "seg%03d.ts"),
            str(stream_dir / "stream.m3u8"),
        ]
        logger.info(f"[HLS] ffmpeg start for {stream_id} seek={start_sec}s")
        ffmpeg_proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        state["ffmpeg_pid"] = ffmpeg_proc.pid
        _, ffmpeg_err = await ffmpeg_proc.communicate()

        ts_files = list(stream_dir.glob("*.ts"))
        if (stream_dir / "stream.m3u8").exists() and ts_files:
            state["status"] = "ready"
            logger.info(f"[HLS] {stream_id} done ({len(ts_files)} segs)")
        else:
            err = ffmpeg_err.decode()[-300:] if ffmpeg_err else ""
            logger.error(f"[HLS] ffmpeg failed {stream_id}: {err}")
            state["status"] = "error"
            state["error"] = "ffmpeg encoding failed"
    except Exception as exc:
        logger.error(f"[HLS] encode exception {stream_id}: {exc}")
        state["status"] = "error"
        state["error"] = str(exc)


async def _run_hls_pipeline(stream_id: str, url: str, stream_dir: Path) -> None:
    """Extract direct URL via yt-dlp -g + get duration, then encode to HLS."""
    state = _active_streams[stream_id]
    try:
        is_youtube = "youtube.com" in url or "youtu.be" in url
        fmt = (
            "bestvideo[height<=480]+bestaudio/best[height<=480]"
            if is_youtube
            else "best[height<=480][ext=mp4]/best[height<=480]/best"
        )

        # Get direct URL + duration in parallel
        ytdlp_cmd = [
            _YTDLP, "-g", "-f", fmt,
            "--no-playlist", "--no-check-certificate",
            "--extractor-args", "youtube:player_client=android",
            url,
        ]
        url_proc, dur = await asyncio.gather(
            asyncio.create_subprocess_exec(
                *ytdlp_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            ),
            _ytdlp_get_duration(url),
        )
        stdout, stderr = await asyncio.wait_for(url_proc.communicate(), timeout=30)

        if url_proc.returncode != 0:
            logger.error(f"[HLS] yt-dlp -g failed ({stream_id}): {stderr.decode()[:200]}")
            state["status"] = "error"
            state["error"] = "URL extraction failed"
            return

        lines = [l.strip() for l in stdout.decode().strip().splitlines() if l.strip()]
        if not lines:
            state["status"] = "error"
            state["error"] = "yt-dlp returned no URL"
            return

        video_url = lines[0]
        audio_url = lines[1] if len(lines) > 1 else None

        # Cache for seek-restart (avoids re-running yt-dlp on every seek)
        state["video_url"] = video_url
        state["audio_url"] = audio_url
        if dur > 0:
            state["duration_sec"] = dur
        logger.info(f"[HLS] {stream_id} url ok, dur={dur}s, audio={'yes' if audio_url else 'no'}")

        await _ffmpeg_encode_hls(
            stream_id, video_url, audio_url, stream_dir, state.get("start_sec", 0)
        )

    except asyncio.TimeoutError:
        state["status"] = "error"
        state["error"] = "yt-dlp timed out (>30s)"
    except Exception as exc:
        logger.error(f"[HLS] Pipeline exception {stream_id}: {exc}")
        state["status"] = "error"
        state["error"] = str(exc)


async def _cleanup_old_streams() -> None:
    """Every 30 min remove stream dirs older than 1 hour."""
    while True:
        await asyncio.sleep(1800)
        now = time.time()
        if _STREAM_DIR.exists():
            for d in _STREAM_DIR.iterdir():
                if d.is_dir() and (now - d.stat().st_mtime) > 3600:
                    try:
                        shutil.rmtree(d)
                        _active_streams.pop(d.name, None)
                        logger.info(f"[HLS CLEANUP] Removed {d.name}")
                    except Exception as exc:
                        logger.warning(f"[HLS CLEANUP] {d.name}: {exc}")


@app.on_event("startup")
async def _startup():
    asyncio.create_task(_cleanup_old_streams())
    asyncio.create_task(subtitle_cleanup_loop())
    # Preload Whisper model pool (Task 5)
    whisper_model = os.getenv("WHISPER_MODEL", "tiny")
    asyncio.create_task(init_model_pool(whisper_model, pool_size=2))
    logger.info(f"[STARTUP] Whisper pool ({whisper_model}×2) preloading...")


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


@app.post("/recommend")
async def recommend(req: RecommendRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    logger.info(f"[RECOMMEND] query={req.query!r} category={req.category}")
    result = await get_recommendations(req.query, req.category)
    if result.get("error") and not result.get("recommendations"):
        raise HTTPException(status_code=503, detail=result["error"])
    return result


@app.get("/tv/{tmdb_id}/seasons")
async def tv_seasons(tmdb_id: int):
    seasons = await get_tv_seasons(tmdb_id)
    return {"tmdb_id": tmdb_id, "seasons": seasons}


@app.get("/tv/{tmdb_id}/season/{n}/episodes")
async def tv_season_episodes(tmdb_id: int, n: int):
    episodes = await get_tv_season_episodes(tmdb_id, n)
    return {"tmdb_id": tmdb_id, "season": n, "episodes": episodes}


@app.post("/film/streams")
async def film_streams(req: FilmStreamsRequest, vk_session: Optional[str] = Cookie(default=None)):
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="title required")
    logger.info(f"[FILM_STREAMS] title={req.title!r} year={req.year}")
    vk_token = get_user_token(vk_session)
    streams = await find_film_streams(req.title, req.year, req.category, vk_token=vk_token)
    return {"streams": streams, "title": req.title, "year": req.year}


@app.post("/subtitle/start")
async def subtitle_start(req: SubtitleStartRequest):
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="url required")
    valid_modes = {"subtitles", "voiceover", "original"}
    if req.mode not in valid_modes:
        raise HTTPException(status_code=400, detail=f"mode must be one of {valid_modes}")
    try:
        key = get_pool().get_best(prefer="gemini")[0].value
    except Exception:
        key = None
    job_id = await subtitle_start_job(
        url=req.url,
        mode=req.mode,
        source_lang=req.source_lang,
        target_lang=req.target_lang,
        gemini_key=key,
    )
    logger.info(f"[SUBTITLE] Started job {job_id} mode={req.mode} lang={req.target_lang}")
    return {"job_id": job_id, "status": "processing"}


@app.get("/subtitle/{job_id}/status")
async def subtitle_status(job_id: str):
    job = subtitle_get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/subtitle/{job_id}/{filename}")
async def subtitle_file(job_id: str, filename: str):
    """Serve generated .vtt or _voice.mp3 from job directory."""
    safe_id = "".join(c for c in job_id if c.isalnum() or c in "-_")
    safe_fn = "".join(c for c in filename if c.isalnum() or c in "-_.")
    if not safe_id or not safe_fn:
        raise HTTPException(status_code=400, detail="Invalid path")
    path = SUBTITLE_JOBS_BASE / safe_id / safe_fn
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if filename.endswith(".vtt"):
        media = "text/vtt; charset=utf-8"
    elif filename.endswith(".mp3"):
        media = "audio/mpeg"
    else:
        raise HTTPException(status_code=400, detail="Unsupported type")
    return Response(
        content=path.read_bytes(),
        media_type=media,
        headers={"Cache-Control": "no-cache", "Access-Control-Allow-Origin": "*"},
    )


@app.post("/hls/start")
async def hls_start(req: HLSStartRequest):
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="url required")
    stream_id = req.stream_id.strip() or uuid.uuid4().hex[:8]
    stream_dir = _STREAM_DIR / stream_id
    stream_dir.mkdir(parents=True, exist_ok=True)
    _active_streams[stream_id] = {
        "status": "processing",
        "started_at": time.time(),
        "url": req.url,
        "start_sec": req.start_sec,
    }
    asyncio.create_task(_run_hls_pipeline(stream_id, req.url, stream_dir))
    logger.info(f"[HLS] Started {stream_id} for {req.url[:80]} (seek={req.start_sec}s)")
    return {
        "stream_id": stream_id,
        "hls_url": f"/hls/{stream_id}/stream.m3u8",
        "status": "processing",
        "start_sec": req.start_sec,
    }


@app.post("/hls/seek")
async def hls_seek(stream_id: str, seek_sec: int):
    """Restart encoding from new position using cached direct URL (fast, no yt-dlp)."""
    state = _active_streams.get(stream_id)
    if not state or "video_url" not in state:
        raise HTTPException(status_code=404, detail="stream not found or URL not cached")

    # Kill current ffmpeg
    pid = state.get("ffmpeg_pid")
    if pid:
        try:
            import signal as _signal
            os.kill(pid, _signal.SIGTERM)
        except Exception:
            pass

    # Clean up old segments
    stream_dir = _STREAM_DIR / stream_id
    for f in stream_dir.glob("*.ts"):
        f.unlink(missing_ok=True)
    (stream_dir / "stream.m3u8").unlink(missing_ok=True)

    state["status"] = "processing"
    state["start_sec"] = seek_sec
    state.pop("ffmpeg_pid", None)

    asyncio.create_task(_ffmpeg_encode_hls(
        stream_id,
        state["video_url"],
        state.get("audio_url"),
        stream_dir,
        seek_sec,
    ))
    logger.info(f"[HLS SEEK] {stream_id} → {seek_sec}s (reuse cached URL)")
    return {"stream_id": stream_id, "status": "processing", "seek_sec": seek_sec,
            "duration_sec": state.get("duration_sec", 0)}


@app.get("/hls/{stream_id}/status")
async def hls_status(stream_id: str):
    stream_dir = _STREAM_DIR / stream_id
    state = _active_streams.get(stream_id, {})
    ts_files = list(stream_dir.glob("*.ts")) if stream_dir.exists() else []
    m3u8_exists = (stream_dir / "stream.m3u8").exists()
    seg_count = len(ts_files)
    if state.get("status") == "error":
        status = "error"
    elif m3u8_exists and seg_count >= 1:
        status = "ready"
        if stream_id in _active_streams:
            _active_streams[stream_id]["status"] = "ready"
    else:
        status = state.get("status", "processing")
    return {
        "stream_id":    stream_id,
        "status":       status,
        "segments":     seg_count,
        "m3u8":         m3u8_exists,
        "duration_sec": state.get("duration_sec", 0),
        "start_sec":    state.get("start_sec", 0),
        "error":        state.get("error"),
    }


_TORRSERVER = "http://127.0.0.1:8099"
_TORRSERVER_PUBLIC = "http://78.17.24.96:8099"
_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts", ".m2ts"}

from providers.torrserver_mirror import select_best_node as _ts_best_node, prefetch_torrent as _ts_prefetch
from providers.torrent_validator import validate_torrent as _validate_torrent, filter_torrents as _filter_torrents


import re as _re

def _extract_hash(magnet: str) -> str:
    """Extract info hash from magnet link."""
    m = _re.search(r'btih:([a-fA-F0-9]{40})', magnet, _re.I)
    return m.group(1).lower() if m else ""


def _build_ts_stream_url(torrent_hash: str, file_id: int) -> str:
    """Build TorrServer stream URL. Hash-only format confirmed working."""
    # TorrServer accepts bare hash as link — simplest and most reliable
    return f"http://78.17.24.96:8081/torrserver/stream?link={torrent_hash}&index={file_id}&play"


def _parse_ts_files(data: dict) -> list:
    """Extract file list from TorrServer stat response (handles nested JSON)."""
    import json as _json
    raw = data.get("data", "")
    if isinstance(raw, str) and raw:
        try:
            inner = _json.loads(raw)
            return inner.get("TorrServer", {}).get("Files", [])
        except Exception:
            pass
    return data.get("file_stats") or []


async def _torrserver_stream(magnet: str, title: str = "") -> dict | None:
    """Add torrent to TorrServer, poll for file metadata, return stream URL."""
    torrent_hash = _extract_hash(magnet)
    if not torrent_hash:
        logger.warning(f"[TORRSERVER] Could not extract hash from magnet")
        return None

    # Use the fastest available TorrServer node
    ts_node = await _ts_best_node()

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # Add using hash directly (avoids encoding issues with tracker URLs)
            await client.post(
                f"{ts_node}/torrents",
                json={"action": "add", "link": magnet,
                      "title": title or "stream", "save_to_db": False},
            )

            # Poll via list — find our hash (stat by hash doesn't work in MatriX)
            files: list = []
            for attempt in range(5):   # max 10s
                await asyncio.sleep(2)
                try:
                    lst = await client.post(
                        f"{ts_node}/torrents",
                        json={"action": "list"},
                    )
                    if lst.status_code == 200 and lst.content:
                        torrents = lst.json() if isinstance(lst.json(), list) else []
                        for t in torrents:
                            if t.get("hash", "").lower() == torrent_hash.lower():
                                files = _parse_ts_files(t)
                                break
                        if files:
                            logger.info(f"[TORRSERVER] Got {len(files)} files after {(attempt+1)*2}s")
                            break
                except Exception as e:
                    logger.debug(f"[TORRSERVER] list attempt {attempt+1}: {e}")

        if not files:
            # TorrServer knows the torrent but no file list yet — use index 0
            logger.info(f"[TORRSERVER] Streaming {torrent_hash[:12]}... at index=0 (no file list)")
            return {
                "stream_url": _build_ts_stream_url(torrent_hash, 0),
                "provider": "torrent", "protocol": "torrserver", "source_type": "hls",
            }

        # Pick best video file: prefer browser-compatible formats over AVI
        _EXT_RANK = {".mkv": 0, ".mp4": 1, ".ts": 2, ".webm": 3, ".avi": 9}

        def _file_score(f: dict) -> tuple:
            path = (f.get("path") or f.get("Name", "")).lower()
            ext  = "." + path.rsplit(".", 1)[-1] if "." in path else ""
            size = f.get("length", f.get("Length", 0))
            return (_EXT_RANK.get(ext, 5), -size)

        video_files = [f for f in files
                       if any((f.get("path") or f.get("Name", "")).lower().endswith(ext)
                              for ext in _VIDEO_EXTS)]

        # Try to match episode by S01E01 pattern from title
        import re as _re2
        _ep_match = _re2.search(r'[Ss](\d+)[Ee](\d+)', title)
        matched_file = None
        if _ep_match and len(video_files) > 1:
            s, e = int(_ep_match.group(1)), int(_ep_match.group(2))
            pats = [
                rf'[Ss]{s:02d}[Ee]{e:02d}',
                rf'[Ss]{s}[Ee]{e}\b',
                rf'\b{e:02d}\b',
            ]
            for f in video_files:
                fn = (f.get("path") or f.get("Name", "")).lower()
                if any(_re2.search(p, fn, _re2.I) for p in pats):
                    matched_file = f
                    break

        best = matched_file or min(video_files or files, key=_file_score)
        idx     = best.get("id", best.get("Id", 0))
        fname   = best.get("path") or best.get("Name", "")
        size_mb = best.get("length", best.get("Length", 0)) // 1_048_576
        fext    = "." + fname.rsplit(".", 1)[-1].lower() if "." in fname else ""

        # Collect all video files as alternative streams
        all_streams = []
        for f in sorted(video_files, key=_file_score):
            fn   = f.get("path") or f.get("Name", "")
            fidx = f.get("id", f.get("Id", 0))
            fext2 = "." + fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
            all_streams.append({
                "stream_url": _build_ts_stream_url(torrent_hash, fidx),
                "file_name":  fn,
                "file_ext":   fext2,
                "size_mb":    f.get("length", f.get("Length", 0)) // 1_048_576,
            })

        logger.info(f"[TORRSERVER] Best: idx={idx} {fext} {size_mb}MB {fname[:60]}"
                    f" (matched={'yes' if matched_file else 'no'}, total={len(all_streams)})")
        return {
            "stream_url":  _build_ts_stream_url(torrent_hash, idx),
            "provider":    "torrent",
            "protocol":    "torrserver",
            "source_type": "hls",
            "file_name":   fname,
            "file_ext":    fext,
            "size_mb":     size_mb,
            "all_streams": all_streams if len(all_streams) > 1 else [],
        }
    except Exception as exc:
        logger.warning(f"[TORRSERVER] Failed: {exc}")
        return None


@app.get("/stream")
async def get_stream_url(url: str, title: str = "", provider: Optional[str] = None, vk_session: Optional[str] = Cookie(default=None)):
    """Universal streaming endpoint — routes to the appropriate provider."""
    if not url:
        raise HTTPException(status_code=400, detail="url required")

    logger.info(f"[STREAM] Routing request for: {url[:60]} (provider suggestion: {provider})")

    # Magnet links → try TorrServer first, fall back to raw magnet (WebTorrent)
    if url.startswith("magnet:"):
        ts = await _torrserver_stream(url, title)
        if ts:
            logger.info(f"[STREAM] TorrServer stream: {ts['stream_url'][:80]}")
            return ts
        logger.info("[STREAM] TorrServer unavailable, returning raw magnet")
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


@app.get("/route")
async def route_query(q: str):
    """Classify user input: URL/magnet/torrent-prefix/search → route type."""
    if not q.strip():
        raise HTTPException(status_code=400, detail="q required")
    return route_to_dict(route_input(q))


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
