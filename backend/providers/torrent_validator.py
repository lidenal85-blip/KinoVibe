"""
torrent_validator.py — Валидация и фильтрация торрент-раздач
Проверяет заявленное качество против реального размера файла и расширений.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("kinovibe.torrent_validator")

# Ожидаемые диапазоны размеров по качеству (МБ)
QUALITY_SIZE_RANGES: dict[str, dict] = {
    "4K":    {"min": 2000,  "max": 60000},
    "2160p": {"min": 2000,  "max": 60000},
    "1080p": {"min": 400,   "max": 6000},
    "720p":  {"min": 150,   "max": 2500},
    "480p":  {"min": 50,    "max": 900},
    "360p":  {"min": 20,    "max": 500},
    "HDR":   {"min": 1500,  "max": 80000},
}

SPAM_KEYWORDS = frozenset({
    "реклама", "спонсор", "промокод", "купить", "скидка",
    "18+", "porn", "xxx", "трейлер", "анонс",
    "сборник", "compilation", "подборка", "sample", "preview",
})

VALID_VIDEO_EXTENSIONS = frozenset({
    ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts", ".m2ts"
})

_QUALITY_PATTERN = re.compile(
    r'\b(4K|2160p|1080p|720p|480p|360p|HDR|BDRip|WEB-DL|Blu-?Ray|DVDRip)\b',
    re.IGNORECASE,
)


def detect_quality(name: str) -> str | None:
    """Определить заявленное качество из названия раздачи."""
    m = _QUALITY_PATTERN.search(name)
    if not m:
        return None
    token = m.group(1).upper()
    # Normalise aliases
    if token in ("BDRIP", "WEB-DL", "BLU-RAY", "BLURAY"):
        return "1080p"
    if token == "DVDRIP":
        return "720p"
    # Normalize resolution tokens to canonical case (1080P → 1080p, 2160P → 2160p)
    for canonical in QUALITY_SIZE_RANGES:
        if token == canonical.upper():
            return canonical
    return token


def validate_torrent(name: str, size_mb: float, files: list[dict] | None = None) -> dict:
    """
    Валидировать торрент по имени, размеру и списку файлов.

    Returns dict with keys:
      is_valid (bool), quality (str|None), reason (str), risk_score (float 0-1)
    """
    name_lower = name.lower()

    # 1 — Спам-ключевые слова
    spam = [kw for kw in SPAM_KEYWORDS if kw in name_lower]
    if spam:
        return {
            "is_valid": False,
            "quality": None,
            "reason": f"Spam keywords: {', '.join(spam)}",
            "risk_score": 1.0,
        }

    # 2 — Качество
    quality = detect_quality(name)
    if not quality:
        return {
            "is_valid": True,
            "quality": "Unknown",
            "reason": "Quality not specified",
            "risk_score": 0.2,
        }

    # 3 — Размер соответствует качеству
    size_range = QUALITY_SIZE_RANGES.get(quality)
    if size_range and size_mb > 0:
        if size_mb < size_range["min"]:
            return {
                "is_valid": False,
                "quality": quality,
                "reason": (
                    f"{quality} claims but only {size_mb:.0f} MB "
                    f"(expected ≥{size_range['min']} MB)"
                ),
                "risk_score": 0.9,
            }
        if size_mb > size_range["max"]:
            return {
                "is_valid": True,
                "quality": quality,
                "reason": f"Larger than expected ({size_mb:.0f} MB) — may be multipart",
                "risk_score": 0.2,
            }

    # 4 — Расширения видеофайлов
    if files:
        has_video = any(
            any((f.get("path") or f.get("Name", "")).lower().endswith(ext)
                for ext in VALID_VIDEO_EXTENSIONS)
            for f in files
        )
        if not has_video:
            return {
                "is_valid": False,
                "quality": quality,
                "reason": "No valid video file found in torrent",
                "risk_score": 0.95,
            }

    return {"is_valid": True, "quality": quality, "reason": "Valid", "risk_score": 0.0}


def filter_torrents(torrents: list[dict]) -> list[dict]:
    """
    Фильтровать список раздач: убрать фейки, добавить поле _validation.
    Возвращает только валидные раздачи.
    """
    result = []
    for t in torrents:
        name = t.get("name") or t.get("title", "")
        raw_size = t.get("size_mb") or t.get("size", 0)
        size_mb = float(raw_size) if raw_size else 0.0
        if size_mb > 10_000:   # raw bytes? convert
            size_mb = size_mb / 1_048_576
        files = t.get("files", [])

        v = validate_torrent(name, size_mb, files)
        t["_validation"] = v
        t["_is_suspicious"] = v["risk_score"] > 0.5

        if v["is_valid"]:
            result.append(t)
        else:
            logger.debug(f"[VALIDATOR] Rejected: {name[:60]} — {v['reason']}")

    return result
