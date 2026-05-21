"""
key_pool.py — Ротация ключей Gemini (7) + Groq (5)
Exponential backoff при 429/500. Thread-safe.
"""

import json
import time
import threading
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

VAULT_PATH = Path(__file__).parent / "vault.json"


@dataclass
class KeyState:
    key: str
    provider: str  # "gemini" | "groq"
    failures: int = 0# ============================================
# core/key_pool.py | VERSION: 2.0.0
# ============================================
from __future__ import annotations
import math
import os
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# Заглушка для лога, если основной модуль LEVIATHAN не импортирован
def log(module, action, details, level="INFO"):
    print(f"[{level}] [{module}] {action} | {details}")

# Cooldown константы
COOLDOWN_429_BASE = 60.0
COOLDOWN_429_MAX  = 3600.0
COOLDOWN_429_JITTER = 0.10
COOLDOWN_403 = 3600.0
COOLDOWN_5XX_BASE = 30.0
COOLDOWN_5XX_MAX  = 300.0

_LOG_DIR  = Path("/storage/emulated/0/Documents/ПРОМТЫ/KINOVIBE/logs")
_LOG_FILE = _LOG_DIR / "api_usage.log"
_LOG_BUF_MAX = 50

class KeyPoolExhaustedError(Exception): pass
class AllExhaustedError(Exception): pass

@dataclass
class KeyEntry:
    key:              str
    index:            int
    provider:         str
    cooldown_until:   float  = 0.0
    last_error:       Optional[int] = None
    calls_ok:         int   = 0
    calls_err:        int   = 0
    tokens_total:     int   = 0
    consecutive_429:  int   = 0
    consecutive_5xx:  int   = 0
    last_used:        float = 0.0

    @property
    def available(self) -> bool:
        return bool(self.key) and time.time() >= self.cooldown_until

    @property
    def value(self) -> str:
        return self.key

    @property
    def cooldown_left(self) -> float:
        return max(0.0, self.cooldown_until - time.time())

    def on_success(self, tokens: int = 0) -> None:
        self.calls_ok       += 1
        self.tokens_total   += tokens
        self.last_used       = time.time()
        self.last_error      = None
        self.consecutive_429 = 0
        self.consecutive_5xx = 0

    def on_error(self, code: int) -> None:
        self.calls_err  += 1
        self.last_error  = code
        if code == 429:
            self.consecutive_429 += 1
            raw_wait = min(COOLDOWN_429_BASE * (2.0 ** (self.consecutive_429 - 1)), COOLDOWN_429_MAX)
            jitter = raw_wait * COOLDOWN_429_JITTER * (random.random() * 2 - 1)
            wait   = max(1.0, raw_wait + jitter)
            self.cooldown_until = time.time() + wait
        elif code == 403:
            self.cooldown_until = time.time() + COOLDOWN_403
        elif code >= 500:
            self.consecutive_5xx += 1
            raw_wait = min(COOLDOWN_5XX_BASE * (2.0 ** (self.consecutive_5xx - 1)), COOLDOWN_5XX_MAX)
            self.cooldown_until = time.time() + raw_wait

    def to_dict(self) -> dict:
        return {
            "index": self.index, "provider": self.provider, 
            "available": self.available, "cooldown_sec": round(self.cooldown_left),
            "calls_ok": self.calls_ok, "calls_err": self.calls_err
        }

class ProviderPool:
    def __init__(self, provider: str, keys: list[str]) -> None:
        self.provider = provider
        self._entries = [KeyEntry(key=k, index=i+1, provider=provider) for i, k in enumerate(keys)]
        self._cursor = 0
        self._lock = threading.Lock()

    def get_key(self) -> KeyEntry:
        with self._lock:
            if not self._entries: raise KeyPoolExhaustedError(f"{self.provider}: No keys")
            n = len(self._entries)
            available = [self._entries[(self._cursor + i) % n] for i in range(n) if self._entries[(self._cursor + i) % n].available]
            if not available: raise KeyPoolExhaustedError(f"{self.provider}: Exhausted")
            chosen = min(available, key=lambda e: (e.consecutive_429, e.last_used))
            self._cursor = (self._entries.index(chosen) + 1) % n
            return chosen

    def report(self, entry: KeyEntry, code: int, tokens: int = 0) -> None:
        if code == 200: entry.on_success(tokens)
        else: entry.on_error(code)

class KeyPool:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = object.__new__(cls)
                cls._instance._ready = False
        return cls._instance

    def init(self) -> KeyPool:
        if self._ready: return self
        self._gemini = ProviderPool("gemini", self._load_keys("GEMINI", 7))
        self._groq = ProviderPool("groq", self._load_keys("GROQ", 5))
        self._ready = True
        return self

    def _load_keys(self, prefix: str, count: int) -> list[str]:
        return [v for i in range(1, count+1) if (v := os.environ.get(f"{prefix}_K{i}", ""))]

    def get_best(self, prefer="gemini"):
        self.init()
        p1 = self._gemini if prefer == "gemini" else self._groq
        p2 = self._groq if prefer == "gemini" else self._gemini
        try: return p1.get_key(), prefer
        except KeyPoolExhaustedError:
            try: return p2.get_key(), "groq" if prefer == "gemini" else "gemini"
            except KeyPoolExhaustedError:
                raise AllExhaustedError("All keys exhausted")

    def report(self, entry, code, tokens=0, latency=0.0, model=""):
        self.init()
        pool = self._gemini if entry.provider == "gemini" else self._groq
        pool.report(entry, code, tokens)
        ts = datetime.now().strftime("%H:%M:%S")
        print(f">> {ts} | {entry.provider}/{model} | K{entry.index} | {tokens}tok | {code} | {latency:.2f}s")

    def status(self):
        self.init()
        return {"gemini": self._gemini._entries, "groq": self._groq._entries}

_pool = None
def get_pool():
    global _pool
    if _pool is None: _pool = KeyPool().init()
    return _pool
    blocked_until: float = 0.0
    total_uses: int = 0

    def is_available(self) -> bool:
        return time.time() >= self.blocked_until

    def backoff(self):
        self.failures += 1
        wait = min(2 ** self.failures, 300)  # max 5 min
        self.blocked_until = time.time() + wait
        logger.warning(f"[KEY_POOL] {self.provider} key blocked {wait}s (failures={self.failures})")

    def reset(self):
        self.failures = 0
        self.blocked_until = 0.0


class KeyPool:
    def __init__(self, vault_path: Path = VAULT_PATH):
        self._lock = threading.Lock()
        self._states: list[KeyState] = []
        self._idx_gemini = 0
        self._idx_groq = 0
        self._load(vault_path)

    def _load(self, path: Path):
        with open(path) as f:
            vault = json.load(f)
        for k in vault.get("gemini_keys", []):
            self._states.append(KeyState(key=k, provider="gemini"))
        for k in vault.get("groq_keys", []):
            self._states.append(KeyState(key=k, provider="groq"))
        logger.info(f"[KEY_POOL] Loaded {len(self._states)} keys total")

    def _get_available(self, provider: str) -> list[KeyState]:
        return [s for s in self._states if s.provider == provider and s.is_available()]

    def get_key(self, provider: str = "gemini") -> Optional[KeyState]:
        """Вернуть следующий доступный ключ по round-robin."""
        with self._lock:
            pool = self._get_available(provider)
            if not pool:
                logger.error(f"[KEY_POOL] No available keys for {provider}")
                return None
            # round-robin внутри доступных
            attr = f"_idx_{provider}"
            idx = getattr(self, attr) % len(pool)
            setattr(self, attr, idx + 1)
            state = pool[idx]
            state.total_uses += 1
            return state

    def mark_failure(self, state: KeyState):
        with self._lock:
            state.backoff()

    def mark_success(self, state: KeyState):
        with self._lock:
            state.reset()

    def status(self) -> dict:
        with self._lock:
            return {
                "gemini": [
                    {"key": s.key[:8] + "...", "available": s.is_available(), "failures": s.failures}
                    for s in self._states if s.provider == "gemini"
                ],
                "groq": [
                    {"key": s.key[:8] + "...", "available": s.is_available(), "failures": s.failures}
                    for s in self._states if s.provider == "groq"
                ],
            }


# Singleton
_pool: Optional[KeyPool] = None


def get_pool() -> KeyPool:
    global _pool
    if _pool is None:
        _pool = KeyPool()
    return _pool
