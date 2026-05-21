from __future__ import annotations
# ============================================
# core/key_pool.py | VERSION: 2.0.1
# DATE: 2026-04-09
# ============================================
import math
import os
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# Логирование
def log(module, action, details, level="INFO"):
    print(f"[{level}] [{module}] {action} | {details}")

# Cooldown константы
COOLDOWN_429_BASE = 60.0
COOLDOWN_429_MAX  = 3600.0
COOLDOWN_429_JITTER = 0.10
COOLDOWN_403 = 3600.0
COOLDOWN_5XX_BASE = 30.0
COOLDOWN_5XX_MAX  = 300.0

# Путь к логам
_LOG_DIR  = Path("/storage/emulated/0/Documents/ПРОМТЫ/KINOVIBE/logs")
_LOG_FILE = _LOG_DIR / "api_usage.log"

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
        return {
            "gemini": [e.to_dict() for e in self._gemini._entries],
            "groq": [e.to_dict() for e in self._groq._entries]
        }

_pool = None
def get_pool():
    global _pool
    if _pool is None: _pool = KeyPool().init()
    return _pool
