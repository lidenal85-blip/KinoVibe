# KinoVibe — HLS Streaming Report v5.2.0
**Дата:** 2026-05-22 | **Backend:** v5.2.0

---

## ЗАДАЧА 1 — Зависимости ✅
```
aria2 1.37.0
ffmpeg 6.1.1-3ubuntu5
```

---

## ЗАДАЧА 2 — HLS эндпоинты ✅

**POST /hls/start**
- Генерирует `stream_id` (uuid4 hex[:8])
- Создаёт `/tmp/kv_streams/{stream_id}/`
- Запускает pipeline: yt-dlp `-g` (android client) → ffmpeg HLS segmentation
- Отвечает мгновенно `{"stream_id", "hls_url", "status":"processing"}`

**GET /hls/{stream_id}/status**
- Считает `.ts` файлы в директории стрима
- `ready` если m3u8 существует и сегментов >= 2
- Возвращает `{status, segments, m3u8, error}`

**Тест:**
```
stream_id: be52ccf8
status: ready через ~25 секунд
segments: 4 (6.2MB total)
```

---

## ЗАДАЧА 3 — Nginx для HLS ✅

**kinovibe-ip (порт 8080):**
```nginx
location /hls/ {
    alias /tmp/kv_streams/;
    add_header Cache-Control no-cache;
    add_header Access-Control-Allow-Origin *;
    types { application/vnd.apple.mpegurl m3u8; video/mp2t ts; }
}
```

**kinovibe-html (порт 8081):** аналогично + `/hls-watch/` → `index.html`

**Тест:** `curl -sI http://localhost:8080/hls/be52ccf8/stream.m3u8`  
→ `Content-Type: application/vnd.apple.mpegurl` ✓

---

## ЗАДАЧА 4 — Cleanup старых стримов ✅

- `@app.on_event("startup")` запускает `asyncio.create_task(_cleanup_old_streams())`
- Каждые 30 минут удаляет директории в `/tmp/kv_streams/` старше 1 часа
- Очищает `_active_streams` словарь

---

## ЗАДАЧА 5+6 — HTML фронтенд ✅

**hls.js 1.4.12** добавлен в `<head>`

**startHLS(url, title):**
- POST `/hls/start` → получает stream_id
- Показывает индикатор "◈ Подготовка потока..."
- Поллинг `/hls/{id}/status` каждые 3 секунды
- При segments >= 2: инициализирует Hls.js плеер
- Fallback на нативный `<video>` для Safari (MSE-supported HLS)

**openPlayer() обновлён:**
- YouTube → iframe embed (без изменений)
- embed source → iframe
- magnet → torrent_player.html
- Любой другой URL → `startHLS()` (новый HLS пайплайн)

**"◈ Смотреть вместе" кнопка:**
- Копирует `window.location.origin + "/hls-watch/" + streamId`
- Открывший ссылку смотрит тот же HLS стрим (те же сегменты в /tmp/kv_streams/)
- Подсвечивается зелёным когда HLS стрим готов

---

## Статус

| Компонент | Статус |
|-----------|--------|
| kinovibe (FastAPI) | ✅ running :8110 |
| Nginx :8080 + /hls/ | ✅ Content-Type m3u8 |
| Nginx :8081 + /hls/ | ✅ |
| HLS pipeline test | ✅ ready 25s, 4 segments |
| Cleanup task | ✅ 30min interval |
| HTML hls.js player | ✅ |

---

## Быстрый тест

```bash
# Запуск HLS стрима
curl -X POST http://localhost:8110/hls/start \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ"}'
# → {"stream_id":"be52ccf8","hls_url":"/hls/be52ccf8/stream.m3u8","status":"processing"}

# Проверка статуса (через ~25с)
curl http://localhost:8110/hls/be52ccf8/status
# → {"status":"ready","segments":4,"m3u8":true}

# HLS файл через nginx
curl -I http://localhost:8080/hls/be52ccf8/stream.m3u8
# → 200 OK, Content-Type: application/vnd.apple.mpegurl
```

---
## subtitle.py v2.0 — Оптимизация (2026-05-23)

### Задачи выполнены (8/8)

| # | Задача | Статус |
|---|--------|--------|
| 1 | WAV 16kHz mono — yt-dlp→ffmpeg resample, удаление tmp файлов | ✅ |
| 2 | VAD фильтр: vad_filter=True, min_silence_duration_ms=500 | ✅ |
| 3 | Кэш SHA256(url+lang), TTL 24ч, in-memory dict | ✅ |
| 4 | Chunked: ffmpeg -f segment -segment_time 600, asyncio.gather параллельно | ✅ |
| 5 | Preload Whisper pool (2×tiny/int8) при startup за ~6 сек | ✅ |
| 6 | Параллельный TTS: asyncio.Queue, consumer генерирует пока Whisper пишет | ✅ |
| 7 | Streaming VTT: append после каждых 25 сегментов, клиент видит результаты инкрементально | ✅ |
| 8 | Автоочистка /tmp/kv_subs/{job_id}/ через 2ч, каждые 30 мин | ✅ |

### Архитектура pipeline

```
URL → yt-dlp/ffmpeg (WAV 16kHz mono)
    → ffmpeg -f segment (10-мин чанки)
    → asyncio.gather [Whisper Pool ×2]  ← параллельные транскрипции
    → asyncio.Queue  ← сегменты по мере готовности
    → Gemini translate (batches 25) + append VTT  ← streaming
    → asyncio.Queue  ← переведённые сегменты
    → edge-tts consumer  ← параллельная озвучка
    → ffmpeg merge (silence-padded)  ← финальный MP3
```

### Производительность (tiny/int8, 2 CPU)
- Pool load: ~6 сек при старте
- 3.5 мин видео: ~15 сек Whisper (4× realtime)
- Первые субтитры доступны через ~20 сек (chunk 0)
- Музыкальный контент: Whisper tiny с VAD не распознаёт пение → normal для STT

### Endpoints
- POST /subtitle/start  → {job_id}
- GET  /subtitle/{job_id}/status  → прогресс + vtt_url + voice_url
- GET  /subtitle/{job_id}/subtitles.vtt  → WebVTT (доступен частично)
- GET  /subtitle/{job_id}/voice.mp3  → merged TTS audio

### Конфигурация
- WHISPER_MODEL=tiny (env var в .env)
- tiny: 39MB RAM, ~4× realtime
- base: 145MB RAM, ~1.5× realtime, лучшее качество
