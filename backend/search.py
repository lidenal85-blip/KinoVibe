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
from providers.tmdb_client import (
    search_movie as tmdb_search_movie,
    search_and_enrich as tmdb_enrich,
    search_tv as tmdb_search_tv,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KINOVIBE_SEARCH")

MODEL_NAME = "gemini-2.5-flash"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SYSTEM_PROMPT = """You are a cinema search expert for Russian-speaking audience.
TASK: Convert user input into optimal YouTube search query.
RULES:
- ALWAYS prioritize Russian dubbed content (add 'русская озвучка' or 'озвучка')
- For mood/feeling queries: analyze emotion → pick genre → form search query
- For title queries: search directly without mood modifiers
- Keep queries 3-6 words maximum
- Popularity filter guidance (apply only when provided):
  - "rare" = arthouse, obscure, festival films, TMDB rating < 6.5, few votes
  - "mid" = quality films, balanced popularity, TMDB rating 6.5–7.5
  - "mainstream" = blockbusters, popular, widely-known, TMDB rating > 7.5
- Return ONLY valid JSON: {"query": "...", "mood": "...", "genre": "...", "language": "ru"}
EXAMPLES:
- 'грустный вечер' → {"query": "драма о любви озвучка full movie", "mood": "sad", "genre": "drama", "language": "ru"}
- 'Аватар' → {"query": "Аватар фильм озвучка", "mood": "", "genre": "sci-fi", "language": "ru"}
- 'что-то смешное' → {"query": "комедия лучшая озвучка full movie", "mood": "happy", "genre": "comedy", "language": "ru"}"""

RECOMMEND_TRIGGERS = [
    "посоветуй", "порекомендуй", "не знаю что", "хочу что-то", "подбери",
    "найди что-нибудь", "хочу посмотреть", "настроение на", "что посмотреть",
    "посмотреть вместе", "хочется", "suggest", "recommend",
]

RECOMMEND_PROMPT = (
    "Ты — опытный кинокуратор с доступом к интернету и актуальным знанием кино.\n"
    "Запрос пользователя: «{query}»\n\n"
    "Найди через поиск и порекомендуй 6-8 фильмов. Требования к подборке:\n"
    "1. НЕ банальный топ-10 — избегай самых очевидных мейнстримных ответов.\n"
    "2. Смешай: 2-3 известных фильма + 2-3 недооценённых или культовых + 1-2 неочевидных открытия.\n"
    "3. Причина (reason) — конкретная, 1-2 предложения: ЧТО именно в этом фильме соответствует запросу.\n"
    "4. Включи фильмы с русской озвучкой, советское/российское кино, если подходит.\n"
    "5. Год должен быть точным.\n\n"
    "Верни ТОЛЬКО валидный JSON без markdown, без текста вне JSON:\n"
    '{{"films": [{{"title": "Название на языке оригинала", "year": 2019, "reason": "Конкретная причина почему именно этот фильм"}}]}}'
)

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


# ─── Kinopoisk enrichment ─────────────────────────────────────────────────────

async def enrich_with_kinopoisk(results: list[dict]) -> list[dict]:
    """Fill missing poster/description/year/rating from Kinopoisk API (kinopoisk.dev)."""
    api_key = os.environ.get("KINOPOISK_API_KEY", "")
    if not api_key:
        return results

    needs = [r for r in results if not r.get("thumbnail") or not r.get("description")]
    if not needs:
        return results

    async def _fetch_one(result: dict) -> None:
        title = result.get("title", "").strip()
        if not title:
            return
        try:
            async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
                r = await client.get(
                    "https://api.kinopoisk.dev/v1.4/movie/search",
                    params={"query": title, "limit": 1},
                    headers={"X-API-KEY": api_key},
                )
            if r.status_code != 200:
                return
            docs = r.json().get("docs", [])
            if not docs:
                return
            doc = docs[0]

            if not result.get("thumbnail"):
                poster = (doc.get("poster") or {}).get("url")
                if poster:
                    result["thumbnail"] = poster

            if not result.get("description"):
                desc = doc.get("description") or doc.get("shortDescription")
                if desc:
                    result["description"] = desc

            # Always enrich with Kinopoisk metadata if available
            if not result.get("year") and doc.get("year"):
                result["year"] = doc["year"]
            kp_rating = (doc.get("rating") or {}).get("kp")
            if kp_rating and not result.get("rating"):
                result["rating"] = round(float(kp_rating), 1)

        except Exception as e:
            logger.debug(f"Kinopoisk lookup failed for '{title}': {e}")

    await asyncio.gather(*[_fetch_one(r) for r in needs], return_exceptions=True)
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

def _has_recommend_trigger(text: str) -> bool:
    """Return True if the query contains a recommendation trigger word."""
    text_lower = text.lower()
    return any(t in text_lower for t in RECOMMEND_TRIGGERS)


async def get_recommendations(user_text: str, category: str = "movies") -> dict:
    """Call Gemini to get film recommendations, then parallel-search each title."""
    pool = get_pool()
    try:
        entry, provider = pool.get_best(prefer="gemini")
    except AllProvidersExhausted as e:
        logger.error(f"get_recommendations: {e}")
        return {"error": "AI service unavailable", "recommendations": [], "count": 0}

    prompt = RECOMMEND_PROMPT.format(query=user_text)
    url = GEMINI_URL.format(model=MODEL_NAME)
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 4096, "temperature": 0.9},
        # Google Search grounding — Gemini searches the internet before answering
        "tools": [{"google_search": {}}],
    }

    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=25) as c:
            r = await c.post(url, json=body, params={"key": entry.value})

        latency = time.monotonic() - t0

        if r.status_code != 200:
            # google_search tool may not be available on this key/tier — retry without grounding
            if r.status_code in (400, 403):
                logger.warning(f"[RECOMMEND] Search grounding failed ({r.status_code}), retrying without grounding")
                body_no_grounding = {k: v for k, v in body.items() if k != "tools"}
                body_no_grounding["generationConfig"]["temperature"] = 0.9
                async with httpx.AsyncClient(timeout=20) as c2:
                    r = await c2.post(url, json=body_no_grounding, params={"key": entry.value})
                if r.status_code != 200:
                    raise Exception(f"{r.status_code}: {r.text[:120]}")
            else:
                raise Exception(f"{r.status_code}: {r.text[:120]}")

        raw_text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        data = _parse_gemini_json(raw_text)
        films = data.get("films", [])

        # Fallback: extract individual film objects with regex if full parse failed
        if not films:
            film_pattern = re.compile(
                r'\{[^{}]*"title"\s*:\s*"([^"]+)"[^{}]*"year"\s*:\s*(\d{4})[^{}]*"reason"\s*:\s*"([^"]+)"[^{}]*\}',
                re.DOTALL,
            )
            for m in film_pattern.finditer(raw_text):
                films.append({"title": m.group(1), "year": int(m.group(2)), "reason": m.group(3)})
            if films:
                logger.info(f"[RECOMMEND] Regex fallback extracted {len(films)} films")

        pool.report(entry, code=200, tokens=len(raw_text) // 4, latency=latency)
        logger.info(f"[RECOMMEND] Gemini returned {len(films)} films for: {user_text!r}")

        # Return fast list — streams loaded lazily per-film by /film/streams
        # Enrich each film with TMDB metadata (poster, rating, description)
        async def _enrich_film(f: dict) -> dict:
            title = f.get("title", "")
            year  = f.get("year")
            tmdb  = await tmdb_enrich(title, year)
            base = {
                "title":  title,
                "year":   year or (tmdb.get("year") if tmdb else None),
                "reason": f.get("reason", ""),
            }
            if tmdb:
                base.update({
                    "poster":      tmdb.get("poster"),
                    "rating":      tmdb.get("rating"),
                    "description": tmdb.get("description") or f.get("reason", ""),
                    "genres":      tmdb.get("genres", []),
                    "tmdb_id":     tmdb.get("tmdb_id"),
                    "title_ru":    tmdb.get("title"),
                    "title_orig":  tmdb.get("title_orig"),
                })
            return base

        gathered_enriched = await asyncio.gather(
            *[_enrich_film(f) for f in films[:8]],
            return_exceptions=True,
        )
        recommendations = [r for r in gathered_enriched if isinstance(r, dict)]

        return {
            "recommendations": recommendations,
            "query": user_text,
            "count": len(recommendations),
            "timestamp": time.time(),
        }

    except Exception as e:
        latency = time.monotonic() - t0
        logger.error(f"get_recommendations failed: {e}")
        pool.report(entry, code=500, latency=latency)
        return {"error": str(e), "recommendations": [], "count": 0}


async def find_film_streams(title: str, year: int | None, category: str = "movies", vk_token: str | None = None) -> list[dict]:
    """
    Search ALL providers simultaneously. Returns up to 8 sources sorted by reliability.
    Priority: torrent > yts > vk > rutube > kodik > hdrezka > filmix > youtube
    """
    year_str = str(year) if year else ""
    query = f"{title} {year_str}".strip()

    _PRIO = {"torrent": 0, "yts": 1, "vk": 2, "rutube": 3,
             "kodik": 4, "hdrezka": 5, "filmix": 6, "youtube": 7}

    all_results = await aggregate_search(query, category, platform="all", vk_token=vk_token)
    all_results.sort(key=lambda r: _PRIO.get(r.provider, 5))

    seen_urls: set[str] = set()
    results: list[dict] = []
    for sr in all_results:
        if sr.url not in seen_urls:
            seen_urls.add(sr.url)
            results.append(sr.to_dict())

    logger.info(f"[FILM_STREAMS] '{title}' ({year}) → {len(results)} streams")
    return results[:8]


def _tmdb_relevance(item: dict, query: str) -> float:
    """Score how well a TMDB result matches the query — higher is better."""
    q = query.lower().strip()
    title = (item.get("title") or "").lower()
    orig  = (item.get("title_orig") or "").lower()
    score = 0.0
    if title == q or orig == q:
        score += 100
    elif title.startswith(q) or orig.startswith(q):
        score += 60
    elif q in title or q in orig:
        score += 30
    score += (item.get("popularity") or 0) * 0.01
    score += (item.get("rating") or 0) * 1.5
    return score


async def search_by_title_tmdb(query: str, category: str = "movies") -> dict:
    """
    Search mode: TMDB movies + TV → merged cards, streams lazy-loaded on click.
    Returns `results` list with TMDB metadata as film cards.
    """
    # 1. Search movies and TV series in parallel
    movie_results, tv_results = await asyncio.gather(
        tmdb_search_movie(query, language="ru-RU", limit=6),
        tmdb_search_tv(query, language="ru-RU", limit=6),
    )

    # 2. If both empty → fallback to aggregate_search
    if not movie_results and not tv_results:
        logger.info(f"[TMDB SEARCH] No results for {query!r}, falling back to aggregate_search")
        provider_results = await aggregate_search(query, category)
        return {
            "metadata": {"query": query, "skipped_ai": True},
            "results": [r.to_dict() for r in provider_results],
            "count": len(provider_results),
            "timestamp": time.time(),
        }

    # 3. Merge and sort by relevance
    all_tmdb = movie_results + tv_results
    all_tmdb.sort(key=lambda x: _tmdb_relevance(x, query), reverse=True)

    # 4. Convert to card format (no stream URL — lazy via /film/streams or /tv/.../seasons)
    results = []
    for m in all_tmdb[:10]:
        if not m.get("title"):
            continue
        results.append({
            "title":       m["title"],
            "year":        m.get("year", ""),
            "rating":      m.get("rating"),
            "poster":      m.get("poster"),
            "thumbnail":   m.get("poster"),
            "description": m.get("description", ""),
            "genres":      m.get("genres", []),
            "duration":    m.get("duration"),
            "provider":    "tmdb",
            "source_type": "tmdb",
            "url":         "",
            "channel":     f"TMDB ★{m.get('rating', '?')} · {m.get('year', '')}",
            "tmdb_id":     m.get("tmdb_id"),
            "title_orig":  m.get("title_orig", ""),
            "media_type":  m.get("media_type", "movie"),
        })

    logger.info(
        f"[TMDB SEARCH] {query!r} → {len(results)} results "
        f"(movies={len(movie_results)}, tv={len(tv_results)})"
    )
    return {
        "metadata": {"query": query, "skipped_ai": True, "source": "tmdb"},
        "results":  results,
        "count":    len(results),
        "timestamp": time.time(),
    }


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

    # Auto-detect recommend mode from trigger words
    if _has_recommend_trigger(user_text):
        logger.info(f"[SEARCH→RECOMMEND] Trigger detected in: {user_text!r}")
        return await get_recommendations(user_text, category)

    cached = await _cache.get(user_text, category, platform, popularity)
    if cached is not None:
        logger.info(f"[CACHE HIT] '{user_text}' ({category}|{platform}|{popularity})")
        return cached

    suffix = CATEGORY_SUFFIX.get(category, "full movie")
    fast_query = f"{user_text} {suffix}"

    skip_gemini = (mode == "search") or _is_title_query(user_text)

    # ── Конкретный провайдер выбран → прямой поиск без AI и TMDB ───────────────
    _DIRECT_PLATFORMS = {"youtube", "vk", "rutube", "filmix", "hdrezka", "kodik"}
    if mode == "search" and platform in _DIRECT_PLATFORMS:
        logger.info(f"[SEARCH] Direct platform={platform} for {user_text!r} — no AI, no TMDB")
        direct_res = await aggregate_search(user_text.strip(), category, platform=platform)
        results = [r.to_dict() for r in direct_res]
        payload = {
            "metadata": {"query": user_text, "platform": platform, "skipped_ai": True},
            "results": results,
            "count": len(results),
            "timestamp": time.time(),
            "platform": platform,
            "popularity": popularity,
        }
        await _cache.set(user_text, category, payload, platform, popularity)
        return payload

    # ── Торрент — TorAPI без TMDB ────────────────────────────────────────────
    if mode == "search" and platform == "torrent":
        torrent_res = await aggregate_search(user_text.strip(), category, platform="torrent")
        results = [r.to_dict() for r in torrent_res]
        payload = {
            "metadata": {"query": user_text, "skipped_ai": True},
            "results": results,
            "count": len(results),
            "timestamp": time.time(),
            "platform": platform,
            "popularity": popularity,
        }
        await _cache.set(user_text, category, payload, platform, popularity)
        return payload

    # ── Search mode + all → TMDB-first (фильмы с постерами) ─────────────────
    if mode == "search":
        logger.info(f"[SEARCH] Mode=search+all → TMDB pipeline for {user_text!r}")
        return await search_by_title_tmdb(user_text.strip(), category)

    # ── Mood / recommend auto-detect ─────────────────────────────────────────
    if skip_gemini:
        logger.info(f"[SEARCH] Direct title search — skipping Gemini (mode={mode})")
        target_query = user_text.strip()
        refined_data = {"query": target_query, "language": "ru", "skipped_ai": True}
    else:
        refined_data = await ai_query_refiner(user_text, category, popularity)
        target_query = refined_data.get("query", fast_query)
    logger.info(f"Refined query: {target_query} | mood={refined_data.get('mood')} genre={refined_data.get('genre')}")

    provider_results = await aggregate_search(target_query, category, platform=platform)
    results = [item.to_dict() for item in provider_results]
    logger.info(f"[SEARCH] {len(results)} results from providers (platform={platform})")

    results = await enrich_with_tmdb(results)
    results = await enrich_with_kinopoisk(results)
    results = await enrich_with_groq(results)

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
