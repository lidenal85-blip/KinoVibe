# ============================================
# server/search.py
# DATE: 2026-05-21
# VERSION: 7.0.0
# PURPOSE: Multi-Provider Search Pipeline with Russian priority,
#          mood-based search, popularity filter, and platform routing
# ============================================

import json
import os
import re
import asyncio
import time
import logging
import hashlib
import httpx
import sys
sys.path.insert(0, "/opt/leviathan_engine")
try:
    from dotenv import load_dotenv
    load_dotenv("/opt/leviathan_engine/.env")
    load_dotenv("/var/www/kinovibe/backend/.env", override=False)
except ImportError:
    pass
from core.key_pool import get_pool, AllProvidersExhausted
from providers import aggregate_search

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KINOVIBE_SEARCH")

MODEL_NAME = "gemini-2.5-flash"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SYSTEM_PROMPT = """You are a cinema expert AI specializing in Russian-language content.
Task: Convert user mood/description into a precise movie/series search query.

Rules:
1. Result must be ONLY valid JSON — no markdown, no explanation.
2. Always prioritize Russian-language content, Russian dubbing, or Russian subtitles.
3. Analyze user mood/feeling → determine fitting genre/style/atmosphere → form a precise search query.
4. Query must be 3-7 keywords in English (for global search compatibility).
5. Always append the specific category suffix to the query.
6. Popularity filter guidance (apply only when provided):
   - "rare" = arthouse, obscure, festival films, TMDB rating < 6.5, few votes
   - "mid" = quality films, balanced popularity, TMDB rating 6.5–7.5
   - "mainstream" = blockbusters, popular, widely-known, TMDB rating > 7.5

Output format (strictly): {"query": "string", "genre": "string", "mood": "string", "language": "ru"}"""

CATEGORY_SUFFIX = {
    "movies": "full movie",
    "series": "full series episode 1",
    "shorts": "short film",
    "anime": "anime full episode",
}

# ─── Production-grade async LRU cache ────────────────────────────────────────

_CACHE_TTL = 300
_CACHE_MAX = 200
_CACHE_SOFT_MAX = 250


class _CacheEntry:
    __slots__ = ("payload", "created_at", "hits", "last_access")

    def __init__(self, payload: dict):
        self.payload = payload
        self.created_at = time.monotonic()
        self.hits = 0
        self.last_access = self.created_at

    def is_fresh(self) -> bool:
        return (time.monotonic() - self.created_at) < _CACHE_TTL

    def touch(self) -> dict:
        self.hits += 1
        self.last_access = time.monotonic()
        return self.payload


class SearchCache:
    """Thread-safe (asyncio) LRU cache with TTL, hit-counting, and stats."""

    def __init__(self):
        self._store: dict[str, _CacheEntry] = {}
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    @staticmethod
    def _make_key(user_text: str, category: str, platform: str = "all", popularity: str = "all") -> str:
        raw = f"{user_text.strip().lower()}|{category}|{platform}|{popularity}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    async def get(self, user_text: str, category: str, platform: str = "all", popularity: str = "all"):
        key = self._make_key(user_text, category, platform, popularity)
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            if not entry.is_fresh():
                del self._store[key]
                self._misses += 1
                return None
            self._hits += 1
            return entry.touch()

    async def set(self, user_text: str, category: str, payload: dict, platform: str = "all", popularity: str = "all"):
        key = self._make_key(user_text, category, platform, popularity)
        async with self._lock:
            self._store[key] = _CacheEntry(payload)
            if len(self._store) >= _CACHE_SOFT_MAX:
                self._evict_lru()

    def _evict_lru(self):
        now = time.monotonic()
        stale = [k for k, e in self._store.items() if (now - e.created_at) >= _CACHE_TTL]
        for k in stale:
            del self._store[k]
            self._evictions += 1
        if len(self._store) > _CACHE_MAX:
            overflow = len(self._store) - _CACHE_MAX
            lru_keys = sorted(self._store, key=lambda k: self._store[k].last_access)[:overflow]
            for k in lru_keys:
                del self._store[k]
                self._evictions += 1

    async def invalidate(self, user_text: str, category: str, platform: str = "all", popularity: str = "all") -> bool:
        key = self._make_key(user_text, category, platform, popularity)
        async with self._lock:
            existed = key in self._store
            self._store.pop(key, None)
            return existed

    async def clear(self):
        async with self._lock:
            count = len(self._store)
            self._store.clear()
            self._evictions += count

    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "entries": len(self._store),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 3) if total else 0.0,
            "evictions": self._evictions,
            "ttl_seconds": _CACHE_TTL,
            "max_entries": _CACHE_MAX,
        }


_cache = SearchCache()


def get_cache() -> SearchCache:
    return _cache


# ─── AI query refiner ─────────────────────────────────────────────────────────

def _parse_gemini_json(raw: str) -> dict:
    """Robustly extract a JSON object from a Gemini response.

    Handles: markdown fences, leading/trailing text, truncated responses.
    Falls back to an empty dict so the caller can still proceed.
    """
    # Strip markdown code fences
    text = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()

    # Try the full cleaned text first (fast path for well-formed responses)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Find the first {...} block using a balanced-brace scan
    start = text.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break

    # Last resort: greedy regex for any {...} span (handles truncated JSON)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass

    logger.warning(f"Could not parse Gemini JSON; raw snippet: {raw[:120]!r}")
    return {}


async def ai_query_refiner(user_prompt: str, category: str, popularity: str = "all") -> dict:
    """Transform query via Gemini API (httpx, no SDK). Supports mood, Russian priority, popularity."""
    pool = get_pool()
    suffix = CATEGORY_SUFFIX.get(category, "full movie")

    try:
        entry, provider = pool.get_best(prefer="gemini")
    except AllProvidersExhausted as e:
        logger.error(f"Critical: {e}")
        return {"query": f"{user_prompt} {suffix}", "fallback": True, "language": "ru"}

    popularity_hint = ""
    if popularity and popularity != "all":
        hints = {
            "rare": "Prefer obscure arthouse/festival films with TMDB rating below 6.5 and few votes.",
            "mid": "Prefer moderately popular quality films with TMDB rating between 6.5 and 7.5.",
            "mainstream": "Prefer popular blockbusters with TMDB rating above 7.5 and many votes.",
        }
        popularity_hint = f"\nPopularity filter: {hints.get(popularity, '')}"

    t0 = time.monotonic()
    full_prompt = (
        f"{SYSTEM_PROMPT}{popularity_hint}\n\n"
        f"User input: '{user_prompt}'. Category: '{category}'. Suffix: '{suffix}'."
    )
    url = GEMINI_URL.format(model=MODEL_NAME)
    body = {
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {"maxOutputTokens": 256, "temperature": 0.2}
    }

    try:
        async with httpx.AsyncClient(timeout=6) as c:
            r = await c.post(url, json=body, params={"key": entry.value})

        latency = time.monotonic() - t0

        if r.status_code != 200:
            raise Exception(f"{r.status_code}: {r.text[:120]}")

        raw_text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        data = _parse_gemini_json(raw_text)

        tokens = len(raw_text) // 4
        pool.report(entry, code=200, tokens=tokens, latency=latency)
        return data

    except Exception as e:
        latency = time.monotonic() - t0
        logger.warning(f"AI Refiner failed: {e}")
        err_code = 429 if "429" in str(e) or "quota" in str(e).lower() else 500
        pool.report(entry, code=err_code, latency=latency)
        return {"query": f"{user_prompt} {suffix}", "error": str(e), "language": "ru"}


# ─── TMDB enrichment ──────────────────────────────────────────────────────────

async def enrich_with_tmdb(results: list[dict]) -> list[dict]:
    """Fill missing thumbnails and descriptions from TMDB for movie results."""
    api_key = os.environ.get("TMDB_API_KEY", "")
    if not api_key:
        return results

    async def _fetch_one(result: dict) -> None:
        # Skip if already has thumbnail
        if result.get("thumbnail"):
            # Still try to fill description if missing
            if result.get("description"):
                return
        title = result.get("title", "")
        if not title:
            return
        try:
            url = "https://api.themoviedb.org/3/search/movie"
            headers = {"Authorization": f"Bearer {api_key}"}
            params = {"query": title, "language": "ru-RU"}
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(url, params=params, headers=headers)
            if r.status_code != 200:
                return
            tmdb_results = r.json().get("results", [])
            if not tmdb_results:
                return
            first = tmdb_results[0]
            if not result.get("thumbnail"):
                poster_path = first.get("poster_path")
                if poster_path:
                    result["thumbnail"] = f"https://image.tmdb.org/t/p/w300{poster_path}"
            if not result.get("description"):
                overview = first.get("overview", "")
                if overview:
                    result["description"] = overview
        except Exception as e:
            logger.debug(f"TMDB lookup failed for '{result.get('title')}': {e}")

    await asyncio.gather(*[_fetch_one(r) for r in results], return_exceptions=True)
    return results


# ─── Groq description enrichment ──────────────────────────────────────────────

async def enrich_with_groq(results: list[dict]) -> list[dict]:
    """Fill still-empty descriptions using Groq llama-3.1-8b-instant."""
    needs_desc = [r for r in results if not r.get("description")]
    if not needs_desc:
        return results

    pool = get_pool()
    try:
        entry, provider = pool.get_best(prefer="groq")
    except AllProvidersExhausted:
        logger.debug("No Groq key available for description enrichment")
        return results
    except Exception:
        return results

    groq_key = entry.value

    async def _fetch_desc(result: dict) -> None:
        title = result.get("title", "")
        if not title:
            return
        prompt = (
            f"Напиши краткое описание фильма '{title}' в 2 предложениях на русском языке. "
            "Только текст описания, без заголовков."
        )
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {groq_key}"},
                    json={
                        "model": "llama-3.1-8b-instant",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.3,
                        "max_tokens": 150,
                    },
                )
            if r.status_code == 200:
                text = r.json()["choices"][0]["message"]["content"].strip()
                if text:
                    result["description"] = text
        except Exception as e:
            logger.debug(f"Groq description failed for '{title}': {e}")

    await asyncio.gather(*[_fetch_desc(r) for r in needs_desc], return_exceptions=True)
    return results


# ─── Main entry point ─────────────────────────────────────────────────────────

_MOOD_WORDS = {
    "хочу", "хочется", "грустно", "грустный", "грустное", "настроение",
    "атмосфера", "атмосферный", "чувствую", "чувство", "посоветуй",
    "посоветуйте", "эмоция", "эмоциональный", "хотеть", "ищу",
    "подбери", "подберите", "помоги", "порекомендуй", "recommend",
    "suggest", "feel", "mood", "вайб", "vibes",
}


def _is_title_query(text: str) -> bool:
    """Return True if the query looks like a direct title (not a mood description).

    Criteria: fewer than 5 words AND no mood/feeling words.
    Examples that return True:  "КВН", "Интерстеллар", "Breaking Bad", "Аватар"
    Examples that return False: "хочу что-то грустное", "посоветуй боевик"
    """
    words = text.strip().lower().split()
    if len(words) >= 5:
        return False
    return not any(w in _MOOD_WORDS for w in words)


async def execute_search(
    user_text: str,
    category: str = "movies",
    platform: str = "all",
    popularity: str = "all",
    mode: str = "mood",
):
    """Primary entry: AI refinement + parallel provider aggregation with LRU cache.

    mode="mood"   → always run Gemini refinement (default, free-form descriptions)
    mode="search" → skip Gemini and use the query directly (exact title lookup)
    Auto-detection: if mode is "mood" but the query looks like a short title,
    Gemini is also skipped to avoid garbling specific names like "КВН".
    """
    logger.info(f"Starting search: '{user_text}' [{category}] platform={platform} popularity={popularity} mode={mode}")

    cached = await _cache.get(user_text, category, platform, popularity)
    if cached is not None:
        logger.info(f"[CACHE HIT] '{user_text}' ({category}|{platform}|{popularity})")
        return cached

    suffix = CATEGORY_SUFFIX.get(category, "full movie")
    fast_query = f"{user_text} {suffix}"

    skip_gemini = (mode == "search") or _is_title_query(user_text)

    if skip_gemini:
        logger.info(f"[SEARCH] Direct title search — skipping Gemini and suffix (mode={mode})")
        # Use the raw query without any suffix so exact titles like "КВН" work
        target_query = user_text.strip()
        refined_data = {"query": target_query, "language": "ru", "skipped_ai": True}
    else:
        refined_data = await ai_query_refiner(user_text, category, popularity)
        target_query = refined_data.get("query", fast_query)
    logger.info(f"Refined query: {target_query} | mood={refined_data.get('mood')} genre={refined_data.get('genre')}")

    provider_results = await aggregate_search(target_query, category, platform=platform)
    results = [item.to_dict() for item in provider_results]

    logger.info(f"[SEARCH] >> {len(results)} results from providers (platform={platform})")

    # Enrich with TMDB posters/descriptions
    results = await enrich_with_tmdb(results)
    logger.info("[SEARCH] TMDB enrichment done")

    # Enrich remaining empty descriptions with Groq
    results = await enrich_with_groq(results)
    logger.info("[SEARCH] Groq enrichment done")

    payload = {
        "metadata":   refined_data,
        "results":    results,
        "count":      len(results),
        "timestamp":  time.time(),
        "platform":   platform,
        "popularity": popularity,
    }

    await _cache.set(user_text, category, payload, platform, popularity)
    return payload


if __name__ == "__main__":
    async def test():
        res = await execute_search("хочу что-то грустное и глубокое", "movies", popularity="rare")
        print(json.dumps(res, indent=2, ensure_ascii=False))
        stats = _cache.stats()
        print(f"\nCache stats: {stats}")
    asyncio.run(test())
