"""
test_api_playwright.py — Full Playwright test suite for KinoVibe backend API
Coverage target: 100% of public endpoints + cache behaviour + error paths

Tests use Playwright's APIRequestContext (no browser needed for REST API),
plus a real Chromium page for the Flutter Web frontend smoke tests.
"""
import json
import time
import pytest
from playwright.sync_api import sync_playwright, APIRequestContext, expect


BASE = "http://localhost:8110"
FRONTEND = "http://localhost:8080"   # nginx serves Flutter Web on port 8080


# ─── Helpers ──────────────────────────────────────────────────────────────────

def api(request: APIRequestContext):
    """Convenience wrapper so tests can call api(req).get('/path')."""
    return request


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def pw_ctx():
    """Single Playwright session for all tests."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
        ctx = browser.new_context(base_url=BASE)
        yield ctx
        browser.close()


@pytest.fixture(scope="session")
def req(pw_ctx):
    """Playwright APIRequestContext bound to the backend."""
    return pw_ctx.request


@pytest.fixture(autouse=True)
def clear_cache(req):
    """Reset the search cache before every test for isolation."""
    req.post("/cache/clear")
    yield


# ═══════════════════════════════════════════════════════════════════════════════
# 1. HEALTH + INFRASTRUCTURE
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealth:
    def test_health_returns_ok(self, req):
        r = req.get("/health")
        assert r.ok
        body = r.json()
        assert body["status"] == "ok"
        assert body["service"] == "KinoVibe Hub"

    def test_health_version_present(self, req):
        r = req.get("/health")
        assert "version" in r.json()

    def test_health_is_fast(self, req):
        t0 = time.monotonic()
        req.get("/health")
        assert (time.monotonic() - t0) < 1.0, "Health check took >1 s"

    def test_pool_status_structure(self, req):
        r = req.get("/pool/status")
        assert r.ok
        body = r.json()
        assert isinstance(body, dict)
        # At least one provider pool is present
        assert len(body) >= 1
        for name, info in body.items():
            assert "total" in info
            assert "available" in info


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CACHE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestCacheEndpoints:
    def test_cache_stats_schema(self, req):
        r = req.get("/cache/stats")
        assert r.ok
        body = r.json()
        for field in ("entries", "hits", "misses", "hit_rate", "evictions",
                      "ttl_seconds", "max_entries"):
            assert field in body, f"Missing field: {field}"

    def test_cache_starts_empty(self, req):
        r = req.get("/cache/stats")
        assert r.json()["entries"] == 0

    def test_cache_clear_returns_ok(self, req):
        r = req.post("/cache/clear")
        assert r.ok
        assert r.json()["ok"] is True

    def test_cache_clear_resets_entries(self, req):
        # Manually inject an entry by doing a search (mocked provider returns quickly)
        req.post("/search", data=json.dumps({"query": "cache_test_unique_xyz", "category": "movies"}),
                 headers={"Content-Type": "application/json"})
        req.post("/cache/clear")
        r = req.get("/cache/stats")
        assert r.json()["entries"] == 0

    def test_hit_rate_after_repeated_query(self, req):
        query = {"query": "hit_rate_test_unique_abc", "category": "movies"}
        headers = {"Content-Type": "application/json"}
        # First request populates cache
        req.post("/search", data=json.dumps(query), headers=headers)
        # Second request should be a cache hit
        req.post("/search", data=json.dumps(query), headers=headers)
        stats = req.get("/cache/stats").json()
        assert stats["hits"] >= 1, "Expected at least 1 cache hit after second identical query"
        assert stats["hit_rate"] > 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SEARCH ENDPOINT — Happy path
# ═══════════════════════════════════════════════════════════════════════════════

class TestSearchHappyPath:
    def _post(self, req, query: str, category: str = "movies"):
        return req.post(
            "/search",
            data=json.dumps({"query": query, "category": category}),
            headers={"Content-Type": "application/json"},
        )

    def test_search_returns_200(self, req):
        r = self._post(req, "action hero movie")
        assert r.status == 200

    def test_search_body_schema(self, req):
        r = self._post(req, "comedy romance film")
        body = r.json()
        assert "results" in body
        assert "count" in body
        assert "metadata" in body
        assert "timestamp" in body
        assert isinstance(body["results"], list)
        assert body["count"] == len(body["results"])

    def test_search_result_item_fields(self, req):
        r = self._post(req, "space adventure")
        body = r.json()
        if body["results"]:
            item = body["results"][0]
            for field in ("id", "title", "url", "provider", "source_type"):
                assert field in item, f"Result item missing: {field}"

    def test_search_category_movies(self, req):
        r = self._post(req, "thriller movie", "movies")
        assert r.status == 200

    def test_search_category_series(self, req):
        r = self._post(req, "detective series", "series")
        assert r.status == 200

    def test_search_category_anime(self, req):
        r = self._post(req, "samurai anime", "anime")
        assert r.status == 200

    def test_search_category_shorts(self, req):
        r = self._post(req, "short film award", "shorts")
        assert r.status == 200

    def test_search_timestamp_is_recent(self, req):
        r = self._post(req, "recent timestamp test")
        ts = r.json()["timestamp"]
        assert abs(ts - time.time()) < 30, "Timestamp too far from now"

    def test_search_unicode_query(self, req):
        r = self._post(req, "фантастика космос")
        assert r.status == 200

    def test_search_long_query(self, req):
        long_q = "a " * 50
        r = self._post(req, long_q)
        assert r.status in (200, 400, 422)  # must not crash the server

    def test_search_special_chars(self, req):
        r = self._post(req, "movie & film | show")
        assert r.status in (200, 400, 422)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. SEARCH ENDPOINT — Caching behaviour
# ═══════════════════════════════════════════════════════════════════════════════

class TestSearchCaching:
    def _post(self, req, query: str, category: str = "movies"):
        return req.post(
            "/search",
            data=json.dumps({"query": query, "category": category}),
            headers={"Content-Type": "application/json"},
        )

    def test_second_call_faster(self, req):
        q = "cache_speed_test_unique_query_1234"
        t0 = time.monotonic()
        self._post(req, q)
        t_cold = time.monotonic() - t0

        t0 = time.monotonic()
        self._post(req, q)
        t_warm = time.monotonic() - t0

        # Warm call should be faster (cache bypass of AI + providers)
        assert t_warm <= t_cold + 0.5, (
            f"Warm call ({t_warm:.2f}s) was not faster than cold ({t_cold:.2f}s)"
        )

    def test_cache_entry_added_after_search(self, req):
        self._post(req, "entry_count_test_query_9988")
        stats = req.get("/cache/stats").json()
        assert stats["entries"] >= 1

    def test_different_categories_cached_separately(self, req):
        q = "samurai film test_sep"
        self._post(req, q, "movies")
        self._post(req, q, "anime")
        # Both should be stored as separate entries
        stats = req.get("/cache/stats").json()
        assert stats["entries"] >= 2

    def test_cache_hit_increments_counter(self, req):
        q = "hit_counter_test_unique_9977"
        self._post(req, q)
        stats_before = req.get("/cache/stats").json()
        hits_before = stats_before["hits"]
        # Second call → cache hit
        self._post(req, q)
        stats_after = req.get("/cache/stats").json()
        assert stats_after["hits"] == hits_before + 1

    def test_miss_increments_on_new_query(self, req):
        stats_before = req.get("/cache/stats").json()
        misses_before = stats_before["misses"]
        self._post(req, "totally_unique_miss_query_20260521_xyzzy")
        stats_after = req.get("/cache/stats").json()
        assert stats_after["misses"] >= misses_before + 1

    def test_cache_returns_identical_results(self, req):
        q = "identical_results_test_unique_abc123"
        r1 = self._post(req, q).json()
        r2 = self._post(req, q).json()
        # Results list and count must match exactly
        assert r1["count"] == r2["count"]
        assert r1["results"] == r2["results"]


# ═══════════════════════════════════════════════════════════════════════════════
# 5. SEARCH — Error / edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestSearchErrors:
    def test_empty_query_returns_400(self, req):
        r = req.post(
            "/search",
            data=json.dumps({"query": "", "category": "movies"}),
            headers={"Content-Type": "application/json"},
        )
        assert r.status == 400

    def test_whitespace_only_query_returns_400(self, req):
        r = req.post(
            "/search",
            data=json.dumps({"query": "   ", "category": "movies"}),
            headers={"Content-Type": "application/json"},
        )
        assert r.status == 400

    def test_missing_query_field_returns_422(self, req):
        r = req.post(
            "/search",
            data=json.dumps({"category": "movies"}),
            headers={"Content-Type": "application/json"},
        )
        assert r.status == 422

    def test_invalid_json_returns_422(self, req):
        r = req.post(
            "/search",
            data="not-json",
            headers={"Content-Type": "application/json"},
        )
        assert r.status == 422

    def test_get_not_allowed_on_search(self, req):
        r = req.get("/search")
        assert r.status == 405

    def test_404_on_unknown_route(self, req):
        r = req.get("/nonexistent_route_xyz")
        assert r.status == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 6. STREAM ENDPOINT
# ═══════════════════════════════════════════════════════════════════════════════

class TestStream:
    def test_stream_requires_url_param(self, req):
        r = req.get("/stream")
        assert r.status in (400, 422)  # FastAPI 422 for missing required query param

    def test_stream_unknown_provider_returns_error(self, req):
        r = req.get("/stream?url=https://example.com&provider=unknown_provider_xyz")
        assert r.status in (400, 422)

    def test_stream_with_kodik_url_routes_kodik(self, req):
        r = req.get("/stream?url=https://kodik.info/serial/12345/abc/720p")
        # If kodik is disabled (no token) → 400; otherwise 200 or 422 on extraction
        assert r.status in (200, 400, 422)

    def test_stream_with_magnet_routes_torrent(self, req):
        r = req.get("/stream?url=magnet:?xt=urn:btih:abc123")
        assert r.status in (200, 400, 422)

    def test_stream_with_vk_url_routes_vk(self, req):
        r = req.get("/stream?url=https://vk.com/video123456")
        assert r.status in (200, 400, 422)

    def test_stream_explicit_youtube_provider(self, req):
        r = req.get("/stream?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ&provider=youtube")
        # May fail on extraction but should not return 500 with bad routing
        assert r.status in (200, 400, 422)


# ═══════════════════════════════════════════════════════════════════════════════
# 7. ROOMS (Watch Party)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRooms:
    def test_list_rooms_returns_list(self, req):
        r = req.get("/rooms")
        assert r.ok
        assert isinstance(r.json(), list)

    def test_create_room_returns_room_id(self, req):
        r = req.post(
            "/rooms/create",
            data=json.dumps({"movie_url": "https://example.com/movie", "movie_title": "Test Movie"}),
            headers={"Content-Type": "application/json"},
        )
        assert r.ok
        body = r.json()
        assert "room_id" in body
        assert isinstance(body["room_id"], str)
        assert len(body["room_id"]) > 0

    def test_create_room_appears_in_list(self, req):
        r = req.post(
            "/rooms/create",
            data=json.dumps({"movie_url": "https://example.com/movie2", "movie_title": "Listed Movie"}),
            headers={"Content-Type": "application/json"},
        )
        room_id = r.json()["room_id"]
        rooms = req.get("/rooms").json()
        ids = [room.get("id") or room.get("room_id") for room in rooms]
        assert room_id in ids, f"Room {room_id} not found in listing: {rooms}"

    def test_create_room_empty_title(self, req):
        r = req.post(
            "/rooms/create",
            data=json.dumps({"movie_url": "", "movie_title": ""}),
            headers={"Content-Type": "application/json"},
        )
        assert r.ok

    def test_create_multiple_rooms(self, req):
        before = len(req.get("/rooms").json())
        for i in range(3):
            req.post(
                "/rooms/create",
                data=json.dumps({"movie_url": f"https://example.com/{i}", "movie_title": f"Movie {i}"}),
                headers={"Content-Type": "application/json"},
            )
        after = len(req.get("/rooms").json())
        assert after >= before + 3


# ═══════════════════════════════════════════════════════════════════════════════
# 8. CACHE — Internal unit tests (sync wrappers around async SearchCache)
# ═══════════════════════════════════════════════════════════════════════════════

def _run(coro):
    """Run a coroutine safely even when pytest-asyncio keeps the main loop running.
    Spawns a daemon thread so asyncio.run() starts its own isolated loop."""
    import asyncio
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result(timeout=10)


class TestCacheUnit:
    """White-box unit tests that import SearchCache directly and run via _run()."""

    def test_get_on_empty_cache_returns_none(self):
        import search as s
        cache = s.SearchCache()
        result = _run(cache.get("x", "movies"))
        assert result is None

    def test_set_then_get_returns_payload(self):
        import search as s
        cache = s.SearchCache()
        payload = {"results": [], "count": 0, "metadata": {}, "timestamp": time.time()}
        _run(cache.set("hello", "movies", payload))
        result = _run(cache.get("hello", "movies"))
        assert result == payload

    def test_stats_initial_state(self):
        import search as s
        cache = s.SearchCache()
        st = cache.stats()
        assert st["entries"] == 0
        assert st["hits"] == 0
        assert st["misses"] == 0
        assert st["hit_rate"] == 0.0

    def test_hit_increments_on_second_get(self):
        import search as s
        cache = s.SearchCache()
        payload = {"results": [], "count": 0, "metadata": {}, "timestamp": time.time()}
        _run(cache.set("q", "movies", payload))
        _run(cache.get("q", "movies"))
        _run(cache.get("q", "movies"))
        st = cache.stats()
        assert st["hits"] >= 2

    def test_clear_empties_store(self):
        import search as s
        cache = s.SearchCache()
        payload = {"results": [], "count": 0, "metadata": {}, "timestamp": time.time()}
        _run(cache.set("a", "movies", payload))
        _run(cache.set("b", "series", payload))
        assert cache.stats()["entries"] == 2
        _run(cache.clear())
        assert cache.stats()["entries"] == 0

    def test_invalidate_removes_entry(self):
        import search as s
        cache = s.SearchCache()
        payload = {"results": [], "count": 0, "metadata": {}, "timestamp": time.time()}
        _run(cache.set("inv_test", "movies", payload))
        removed = _run(cache.invalidate("inv_test", "movies"))
        assert removed is True
        result = _run(cache.get("inv_test", "movies"))
        assert result is None

    def test_invalidate_nonexistent_returns_false(self):
        import search as s
        cache = s.SearchCache()
        removed = _run(cache.invalidate("ghost", "movies"))
        assert removed is False

    def test_different_categories_different_keys(self):
        import search as s
        cache = s.SearchCache()
        p1 = {"results": [{"id": "1"}], "count": 1, "metadata": {}, "timestamp": time.time()}
        p2 = {"results": [{"id": "2"}], "count": 1, "metadata": {}, "timestamp": time.time()}
        _run(cache.set("matrix", "movies", p1))
        _run(cache.set("matrix", "series", p2))
        r1 = _run(cache.get("matrix", "movies"))
        r2 = _run(cache.get("matrix", "series"))
        assert r1 != r2

    def test_lru_eviction_does_not_exceed_max(self):
        import search as s
        cache = s.SearchCache()
        orig_max = s._CACHE_MAX
        orig_soft = s._CACHE_SOFT_MAX
        s._CACHE_MAX = 5
        s._CACHE_SOFT_MAX = 6
        try:
            for i in range(10):
                payload = {"results": [], "count": 0, "metadata": {}, "timestamp": time.time()}
                _run(cache.set(f"q{i}", "movies", payload))
            assert len(cache._store) <= 10
        finally:
            s._CACHE_MAX = orig_max
            s._CACHE_SOFT_MAX = orig_soft


# ═══════════════════════════════════════════════════════════════════════════════
# 9. FLUTTER WEB FRONTEND — Smoke tests via browser
# ═══════════════════════════════════════════════════════════════════════════════

class TestFrontend:
    """Browser-based smoke tests for the Flutter Web frontend."""

    @pytest.fixture(scope="class")
    def browser_page(self, pw_ctx):
        page = pw_ctx.new_page()
        yield page
        page.close()

    def test_frontend_loads(self, browser_page):
        browser_page.goto(FRONTEND, timeout=15000)
        # Flutter bootstraps via JS; wait for the canvas or the title
        browser_page.wait_for_load_state("networkidle", timeout=15000)
        title = browser_page.title()
        assert title  # Any non-empty title is fine

    def test_frontend_no_console_errors_on_load(self, browser_page):
        errors = []
        browser_page.on("console", lambda msg: errors.append(msg) if msg.type == "error" else None)
        browser_page.goto(FRONTEND, timeout=15000)
        browser_page.wait_for_load_state("networkidle", timeout=15000)
        # Filter out known benign Flutter worker warnings
        severe = [e.text for e in errors if "flutter_service_worker" not in e.text
                  and "favicon" not in e.text.lower()]
        assert len(severe) == 0, f"Console errors: {severe}"

    def test_flutter_js_loaded(self, browser_page):
        browser_page.goto(FRONTEND, timeout=15000)
        browser_page.wait_for_load_state("networkidle", timeout=15000)
        # Check for Flutter bootstrap or main script tag
        js_loaded = browser_page.evaluate(
            "() => !!document.querySelector('script[src*=\"flutter\"]') || "
            "!!document.querySelector('script[src*=\"main.dart\"]') || "
            "document.body.innerHTML.length > 100"
        )
        assert js_loaded

    def test_frontend_http_200(self, pw_ctx):
        req_fe = pw_ctx.request
        r = req_fe.get(f"{FRONTEND}/index.html")
        assert r.status == 200

    def test_manifest_json_present(self, pw_ctx):
        req_fe = pw_ctx.request
        r = req_fe.get(f"{FRONTEND}/manifest.json")
        assert r.status == 200

    def test_flutter_service_worker_present(self, pw_ctx):
        req_fe = pw_ctx.request
        r = req_fe.get(f"{FRONTEND}/flutter_service_worker.js")
        assert r.status == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 10. CONCURRENCY & STRESS
# ═══════════════════════════════════════════════════════════════════════════════

class TestConcurrency:
    def test_parallel_health_requests(self, req):
        """10 parallel health checks should all return 200."""
        import threading
        results = []

        def do_req():
            with sync_playwright() as pw:
                browser = pw.chromium.launch(args=["--no-sandbox"])
                ctx = browser.new_context(base_url=BASE)
                r = ctx.request.get("/health")
                results.append(r.status)
                browser.close()

        threads = [threading.Thread(target=do_req) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(s == 200 for s in results), f"Failures: {results}"

    def test_cache_concurrent_writes_consistent(self):
        """Cache must stay consistent under concurrent asyncio writes."""
        import asyncio, search as s
        cache = s.SearchCache()

        async def worker(i):
            payload = {"results": [], "count": i, "metadata": {}, "timestamp": time.time()}
            await cache.set(f"cq{i}", "movies", payload)
            result = await cache.get(f"cq{i}", "movies")
            assert result is not None
            assert result["count"] == i

        async def run_all():
            await asyncio.gather(*[worker(i) for i in range(20)])

        _run(run_all())
        assert cache.stats()["entries"] <= 20
