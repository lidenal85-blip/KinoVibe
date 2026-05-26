"""
subtitle.py v2.0 — Optimized subtitle / TTS pipeline
Tasks implemented:
  1. WAV 16kHz mono extraction
  2. VAD filter (vad_filter=True, min_silence_duration_ms=500)
  3. Cache by SHA256(url+lang), TTL 24h
  4. Chunked parallel transcription via asyncio.gather + model pool
  5. Whisper preloaded at startup via init_model_pool()
  6. Parallel TTS via asyncio.Queue (consumer runs while Whisper processes next chunk)
  7. Streaming VTT: partial file written after each chunk, client polls
  8. Auto-cleanup: job dirs removed 2h after completion, checked every 30 min
"""

from __future__ import annotations
import asyncio
import hashlib
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("kinovibe.subtitle")

# ── Paths ─────────────────────────────────────────────────────────────────────
JOBS_BASE = Path("/tmp/kv_subs")
JOBS_BASE.mkdir(parents=True, exist_ok=True)

YTDLP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "venv", "bin", "yt-dlp")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

CHUNK_SECONDS = 600   # 10 minutes per chunk
TRANSLATE_BATCH = 25  # segments per Gemini call

EDGE_VOICES: dict[str, str] = {
    "ru": "ru-RU-SvetlanaNeural",
    "en": "en-US-JennyNeural",
    "de": "de-DE-KatjaNeural",
    "fr": "fr-FR-DeniseNeural",
    "es": "es-ES-ElviraNeural",
    "ja": "ja-JP-NanamiNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "ko": "ko-KR-SunHiNeural",
    "uk": "uk-UA-PolinaNeural",
}

# ── Task 5: Model pool (preloaded at startup) ─────────────────────────────────
_model_pool: Optional[asyncio.Queue] = None
_pool_ready = asyncio.Event() if False else None  # created lazily in async context


async def init_model_pool(model_name: str = "tiny", pool_size: int = 2) -> None:
    """Preload `pool_size` WhisperModel instances into the pool. Call once at startup."""
    global _model_pool
    _model_pool = asyncio.Queue(maxsize=pool_size)
    loop = asyncio.get_running_loop()

    async def _load_one(idx: int):
        logger.info(f"[Whisper] Loading model {idx+1}/{pool_size} ({model_name}/int8)…")
        m = await loop.run_in_executor(None, _mk_model, model_name)
        await _model_pool.put(m)
        logger.info(f"[Whisper] Model {idx+1} ready")

    await asyncio.gather(*[_load_one(i) for i in range(pool_size)])
    logger.info(f"[Whisper] Pool ready: {pool_size}× {model_name}")


def _mk_model(name: str):
    from faster_whisper import WhisperModel
    return WhisperModel(name, device="cpu", compute_type="int8")


async def _acquire_model():
    global _model_pool
    if _model_pool is None:
        # Fallback: lazy single-instance load if startup preload wasn't called
        _model_pool = asyncio.Queue(maxsize=1)
        name = os.getenv("WHISPER_MODEL", "tiny")
        loop = asyncio.get_running_loop()
        m = await loop.run_in_executor(None, _mk_model, name)
        await _model_pool.put(m)
    return await _model_pool.get()


async def _release_model(m):
    await _model_pool.put(m)


# ── Task 3: Cache ─────────────────────────────────────────────────────────────
_CACHE: dict[str, dict] = {}
_CACHE_TTL = 86_400  # 24 hours


def _cache_key(url: str, target_lang: str) -> str:
    return hashlib.sha256(f"{url}|{target_lang}".encode()).hexdigest()[:16]


def _cache_lookup(url: str, target_lang: str) -> Optional[str]:
    k = _cache_key(url, target_lang)
    entry = _CACHE.get(k)
    if not entry:
        return None
    if time.time() - entry["created_at"] > _CACHE_TTL:
        _CACHE.pop(k, None)
        return None
    return entry["job_id"]


def _cache_store(url: str, target_lang: str, job_id: str) -> None:
    _CACHE[_cache_key(url, target_lang)] = {"job_id": job_id, "created_at": time.time()}


# ── Jobs registry ─────────────────────────────────────────────────────────────
_jobs: dict[str, dict] = {}


def get_job(job_id: str) -> Optional[dict]:
    return _jobs.get(job_id)


# ── Public entry point ────────────────────────────────────────────────────────

async def start_job(
    url: str,
    mode: str = "subtitles",
    source_lang: Optional[str] = None,
    target_lang: str = "ru",
    gemini_key: Optional[str] = None,
) -> str:
    # Task 3: cache hit
    cached = _cache_lookup(url, target_lang)
    if cached and _jobs.get(cached, {}).get("status") in ("ready", "processing"):
        logger.info(f"[Subtitle] Cache hit → {cached}")
        return cached

    job_id = hashlib.md5(f"{url}{mode}{target_lang}{time.time()}".encode()).hexdigest()[:10]
    job_dir = JOBS_BASE / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    vtt_path = job_dir / "subtitles.vtt"
    vtt_path.write_text("WEBVTT\n\n", encoding="utf-8")

    _jobs[job_id] = {
        "status": "processing",
        "mode": mode,
        "progress": "Инициализация…",
        "step": 0,
        "steps": 4 if mode == "voiceover" else 3,
        "created_at": time.time(),
        "segments_count": 0,
        "vtt_url": f"/subtitle/{job_id}/subtitles.vtt",
        "job_dir": str(job_dir),
    }
    _cache_store(url, target_lang, job_id)

    asyncio.create_task(_run_pipeline(job_id, url, mode, source_lang, target_lang, gemini_key))
    logger.info(f"[Subtitle] Job {job_id} started mode={mode} lang={target_lang}")
    return job_id


# ── Pipeline ──────────────────────────────────────────────────────────────────

async def _run_pipeline(
    job_id: str,
    url: str,
    mode: str,
    source_lang: Optional[str],
    target_lang: str,
    gemini_key: Optional[str],
) -> None:
    job = _jobs[job_id]
    job_dir = Path(job["job_dir"])
    vtt_path = job_dir / "subtitles.vtt"

    try:
        # ── Step 1: Download audio as WAV 16kHz mono ──────────────────────────
        _upd(job, 1, "Загрузка аудио…")
        audio_path = job_dir / "audio.wav"
        await _download_audio(url, audio_path)
        logger.info(f"[Subtitle {job_id}] Audio ready: {audio_path.stat().st_size // 1024} KB")

        # ── Step 2: Split into 10-min chunks (Task 4) ─────────────────────────
        _upd(job, 2, "Разбивка на фрагменты…")
        chunks = await _split_chunks(audio_path, job_dir)
        audio_path.unlink(missing_ok=True)   # Task 1: delete original immediately
        logger.info(f"[Subtitle {job_id}] {len(chunks)} chunks")

        # ── TTS queue (Task 6) ────────────────────────────────────────────────
        tts_queue: Optional[asyncio.Queue] = None
        tts_parts: list[tuple[dict, Path]] = []   # (segment, mp3_path)
        tts_task = None
        if mode == "voiceover":
            tts_queue = asyncio.Queue()
            voice = EDGE_VOICES.get(target_lang, "ru-RU-SvetlanaNeural")
            tts_task = asyncio.create_task(
                _tts_consumer(tts_queue, job_dir, voice, tts_parts)
            )

        # ── Step 3: Parallel transcription + streaming translate+VTT (Tasks 4,7)
        _upd(job, 3, "Распознавание речи (Whisper)…")
        all_segments = await _transcribe_and_stream(
            job_id, job, chunks, source_lang, target_lang, gemini_key,
            vtt_path, tts_queue,
        )

        # ── Step 4: Voiceover merge ───────────────────────────────────────────
        if tts_queue is not None:
            await tts_queue.put(None)    # sentinel → stop consumer
            if tts_task:
                await tts_task
            if tts_parts:
                _upd(job, 4, "Сборка озвучки…")
                voice_path = job_dir / "voice.mp3"
                await _merge_tts(tts_parts, voice_path, all_segments[-1]["end"] if all_segments else 0)
                if voice_path.exists():
                    job["voice_url"] = f"/subtitle/{job_id}/voice.mp3"

        job["status"]         = "ready"
        job["segments_count"] = len(all_segments)
        job["progress"]       = f"Готово · {len(all_segments)} фраз"
        logger.info(f"[Subtitle {job_id}] Done: {len(all_segments)} segments")

    except Exception as exc:
        logger.error(f"[Subtitle {job_id}] Pipeline error: {exc}", exc_info=True)
        job["status"]   = "error"
        job["error"]    = str(exc)
        job["progress"] = f"Ошибка: {exc}"


# ── Task 4: Chunked parallel transcription + Task 7: streaming VTT ────────────

async def _transcribe_and_stream(
    job_id: str,
    job: dict,
    chunks: list[Path],
    source_lang: Optional[str],
    target_lang: str,
    gemini_key: Optional[str],
    vtt_path: Path,
    tts_queue: Optional[asyncio.Queue],
) -> list[dict]:
    """
    Launch all chunk transcriptions in parallel (using model pool).
    As each chunk finishes, immediately translate (streaming) and append VTT.
    Maintains correct segment order via chunk_index sorting.
    """
    # Queue for completed (chunk_idx, raw_segments) pairs
    done_q: asyncio.Queue = asyncio.Queue()

    async def transcribe_chunk(chunk_path: Path, chunk_idx: int) -> None:
        offset = chunk_idx * CHUNK_SECONDS
        model = await _acquire_model()
        try:
            loop = asyncio.get_running_loop()
            segs = await loop.run_in_executor(
                None, _do_transcribe, model, str(chunk_path), source_lang, offset
            )
            chunk_path.unlink(missing_ok=True)   # Task 1: delete chunk after use
        finally:
            await _release_model(model)
        await done_q.put((chunk_idx, segs))

    # Task 4: launch all transcriptions simultaneously
    tasks = [
        asyncio.create_task(transcribe_chunk(c, i))
        for i, c in enumerate(chunks)
    ]

    # Collector: process results in chunk order (for correct VTT timestamps)
    pending_results: dict[int, list[dict]] = {}
    next_chunk = 0
    seg_counter = 0
    all_segments: list[dict] = []
    detected_lang = "?"

    needs_translate = bool(target_lang and gemini_key)

    for _ in range(len(chunks)):
        chunk_idx, raw_segs = await done_q.get()
        pending_results[chunk_idx] = raw_segs
        if raw_segs:
            detected_lang = raw_segs[0].get("lang", "?")

        # Task 7: process chunks in strict order so VTT timestamps are correct
        while next_chunk in pending_results:
            segs = pending_results.pop(next_chunk)
            job["progress"] = f"Перевод: чанк {next_chunk+1}/{len(chunks)}…"

            # Translate in sub-batches of TRANSLATE_BATCH
            translated: list[dict] = []
            if needs_translate and segs:
                for batch_start in range(0, len(segs), TRANSLATE_BATCH):
                    batch = segs[batch_start: batch_start + TRANSLATE_BATCH]
                    translated_batch = await _translate_batch(batch, target_lang, gemini_key)
                    translated.extend(translated_batch)
                    # Task 7: write partial VTT immediately
                    _append_vtt(translated_batch, vtt_path, seg_counter)
                    seg_counter += len(translated_batch)
                    all_segments.extend(translated_batch)
                    job["segments_count"] = len(all_segments)
                    if tts_queue:
                        for s in translated_batch:
                            await tts_queue.put(s)
            else:
                translated = segs
                _append_vtt(segs, vtt_path, seg_counter)
                seg_counter += len(segs)
                all_segments.extend(segs)
                job["segments_count"] = len(all_segments)
                if tts_queue:
                    for s in segs:
                        await tts_queue.put(s)

            next_chunk += 1
            job["progress"] = (
                f"Whisper + перевод: {next_chunk}/{len(chunks)} чанков · "
                f"{len(all_segments)} фраз ({detected_lang}→{target_lang})"
            )

    await asyncio.gather(*tasks, return_exceptions=True)  # ensure all tasks clean up
    return all_segments


# ── Task 2: Transcription with VAD ───────────────────────────────────────────

def _do_transcribe(model, audio_path: str, source_lang: Optional[str], offset: float) -> list[dict]:
    segs_iter, info = model.transcribe(
        audio_path,
        language=source_lang,
        beam_size=3,
        # Task 2: VAD filter
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        word_timestamps=False,
    )
    return [
        {
            "start": seg.start + offset,
            "end":   seg.end   + offset,
            "text":  seg.text.strip(),
            "lang":  info.language,
        }
        for seg in segs_iter
        if seg.text.strip()
    ]


# ── Task 1: Audio extraction ──────────────────────────────────────────────────

async def _download_audio(url: str, out: Path) -> None:
    """Download/extract audio as WAV 16kHz mono (Task 1)."""
    # Try yt-dlp for web URLs, ffmpeg fallback for direct streams
    if any(h in url for h in ("youtube.com", "youtu.be", "vk.com", "rutube.ru", "ok.ru")):
        if await _ytdlp_to_wav(url, out):
            return
    if await _ffmpeg_to_wav(url, out):
        return
    if await _ytdlp_to_wav(url, out):  # last resort
        return
    raise RuntimeError("Не удалось извлечь аудио из источника")


async def _ytdlp_to_wav(url: str, out: Path) -> bool:
    tmp_stem = str(out.with_suffix(""))
    cmd = [
        YTDLP, "-x", "--audio-format", "wav", "--audio-quality", "5",
        "--no-playlist", "--extractor-args", "youtube:player_client=android",
        "-o", tmp_stem, url,
    ]
    ok = await _run_proc(cmd, timeout=180)
    if ok and out.exists():
        # Re-encode to exactly 16kHz mono (yt-dlp wav may be 44kHz stereo)
        tmp = out.with_suffix(".tmp.wav")
        out.rename(tmp)
        await _ffmpeg_resample(tmp, out)
        tmp.unlink(missing_ok=True)
        return out.exists()
    # yt-dlp may save as different extension
    for ext in (".m4a", ".opus", ".webm", ".mp3"):
        alt = Path(tmp_stem + ext)
        if alt.exists():
            ok2 = await _ffmpeg_resample(alt, out)
            alt.unlink(missing_ok=True)
            return ok2
    return False


async def _ffmpeg_to_wav(url: str, out: Path) -> bool:
    cmd = [
        "ffmpeg", "-y", "-i", url,
        "-vn", "-ar", "16000", "-ac", "1", "-acodec", "pcm_s16le",
        str(out),
    ]
    return await _run_proc(cmd, timeout=120) and out.exists()


async def _ffmpeg_resample(src: Path, dst: Path) -> bool:
    """Convert any audio file to WAV 16kHz mono."""
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-ar", "16000", "-ac", "1", "-acodec", "pcm_s16le",
        str(dst),
    ]
    return await _run_proc(cmd, timeout=60) and dst.exists()


# ── Task 4: Chunk splitting ───────────────────────────────────────────────────

async def _split_chunks(audio: Path, job_dir: Path) -> list[Path]:
    pattern = str(job_dir / "chunk_%03d.wav")
    cmd = [
        "ffmpeg", "-y", "-i", str(audio),
        "-f", "segment", "-segment_time", str(CHUNK_SECONDS),
        "-ar", "16000", "-ac", "1", "-acodec", "pcm_s16le",
        pattern,
    ]
    await _run_proc(cmd, timeout=120)
    chunks = sorted(job_dir.glob("chunk_*.wav"))
    if not chunks:
        # File shorter than one chunk — treat whole file as single chunk
        single = job_dir / "chunk_000.wav"
        audio.rename(single)
        chunks = [single]
    return chunks


# ── Translation (Task 7: streaming batches) ────────────────────────────────────

_LANG_NAMES = {
    "ru": "русский", "en": "английский", "de": "немецкий", "fr": "французский",
    "es": "испанский", "uk": "украинский", "ja": "японский",
    "zh": "китайский", "ko": "корейский",
}

_TRANSLATE_PROMPT = (
    "Переведи субтитры на {lang}.\n"
    "Формат: INDEX|ТЕКСТ — строго одна строка на фразу, не объединяй.\n"
    "Только перевод, без пояснений.\n\n"
    "{lines}"
)


async def _translate_batch(segs: list[dict], target_lang: str, api_key: str) -> list[dict]:
    if not segs:
        return segs
    lang_name = _LANG_NAMES.get(target_lang, target_lang)
    lines = "\n".join(f"{i}|{s['text']}" for i, s in enumerate(segs))
    prompt = _TRANSLATE_PROMPT.format(lang=lang_name, lines=lines)
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.1},
    }
    translated = list(segs)
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(GEMINI_URL, json=body, params={"key": api_key})
        if r.status_code == 200:
            raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            for line in raw.strip().splitlines():
                if "|" in line:
                    idx_s, _, text = line.partition("|")
                    try:
                        idx = int(idx_s.strip())
                        if 0 <= idx < len(segs):
                            translated[idx] = {**segs[idx], "text": text.strip()}
                    except ValueError:
                        pass
    except Exception as e:
        logger.warning(f"[Translate] Batch failed: {e}")
    return translated


# ── VTT writer (Task 7: append-mode) ─────────────────────────────────────────

def _fmt_vtt(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def _append_vtt(segs: list[dict], vtt_path: Path, start_counter: int) -> None:
    if not segs:
        return
    lines: list[str] = []
    for i, seg in enumerate(segs, start=start_counter + 1):
        lines += [
            str(i),
            f"{_fmt_vtt(seg['start'])} --> {_fmt_vtt(seg['end'])}",
            seg["text"],
            "",
        ]
    with vtt_path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ── Task 6: Parallel TTS consumer ────────────────────────────────────────────

async def _tts_consumer(
    queue: asyncio.Queue,
    job_dir: Path,
    voice: str,
    results: list,
) -> None:
    import edge_tts
    idx = 0
    while True:
        seg = await queue.get()
        if seg is None:
            queue.task_done()
            break
        part = job_dir / f"tts_{idx:04d}.mp3"
        try:
            comm = edge_tts.Communicate(seg["text"], voice, rate="+5%")
            await comm.save(str(part))
            results.append((seg, part))
        except Exception as e:
            logger.warning(f"[TTS] Segment {idx} failed: {e}")
            results.append((seg, None))
        idx += 1
        queue.task_done()


async def _merge_tts(
    parts: list[tuple[dict, Optional[Path]]],
    out_path: Path,
    total_dur: float,
) -> None:
    """Merge TTS segments with silence padding to match video timeline."""
    inputs: list[str] = []
    filter_nodes: list[str] = []
    audio_labels: list[str] = []
    sil_idx = 0
    prev_end = 0.0

    for seg, mp3 in parts:
        if mp3 is None or not mp3.exists():
            continue
        gap = seg["start"] - prev_end
        if gap > 0.05:
            node = f"sil{sil_idx}"
            filter_nodes.append(f"aevalsrc=0:c=mono:s=16000:d={gap:.3f}[{node}]")
            audio_labels.append(f"[{node}]")
            sil_idx += 1
        n = len(inputs)
        inputs += ["-i", str(mp3)]
        audio_labels.append(f"[{n}:a]")
        prev_end = seg["end"]

    trail = total_dur - prev_end
    if trail > 0.1:
        node = f"sil{sil_idx}"
        filter_nodes.append(f"aevalsrc=0:c=mono:s=16000:d={trail:.3f}[{node}]")
        audio_labels.append(f"[{node}]")

    if not audio_labels:
        return

    n_streams = len(audio_labels)
    concat_in = "".join(audio_labels)
    filter_complex = (
        (";".join(filter_nodes) + ";" if filter_nodes else "")
        + f"{concat_in}concat=n={n_streams}:v=0:a=1[out]"
    )

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-acodec", "libmp3lame", "-b:a", "96k",
        str(out_path),
    ]
    ok = await _run_proc(cmd, timeout=300)
    if not ok:
        logger.error("[TTS] merge ffmpeg failed")

    # Task 1: clean up individual TTS parts
    for _, mp3 in parts:
        if mp3:
            mp3.unlink(missing_ok=True)


# ── Task 8: Auto-cleanup ──────────────────────────────────────────────────────

async def cleanup_loop() -> None:
    """Remove job dirs older than 2h; also purge cache; runs every 30 min."""
    while True:
        await asyncio.sleep(1800)  # 30 minutes
        now = time.time()
        # Clean jobs
        for job_id, job in list(_jobs.items()):
            age = now - job.get("created_at", now)
            if age > 7200:  # 2 hours
                _jobs.pop(job_id, None)
                job_dir = Path(job.get("job_dir", ""))
                if job_dir.exists():
                    try:
                        shutil.rmtree(job_dir)
                        logger.info(f"[Subtitle CLEANUP] Removed {job_id}")
                    except Exception as e:
                        logger.warning(f"[Subtitle CLEANUP] {job_id}: {e}")
        # Clean cache
        for key in list(_CACHE.keys()):
            if now - _CACHE[key]["created_at"] > _CACHE_TTL:
                _CACHE.pop(key, None)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _run_proc(cmd: list[str], timeout: int = 60) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            logger.debug(f"[proc] {cmd[0]} rc={proc.returncode}: {err.decode()[-200:]}")
        return proc.returncode == 0
    except asyncio.TimeoutError:
        logger.warning(f"[proc] Timeout: {cmd[0]}")
        return False
    except Exception as e:
        logger.warning(f"[proc] Error: {e}")
        return False


def _upd(job: dict, step: int, text: str) -> None:
    job["step"] = step
    job["progress"] = text
