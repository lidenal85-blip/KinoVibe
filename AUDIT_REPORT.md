# AUDIT REPORT KinoVibe — 2026-05-22 00:25:15

## 1. Сервисы и порты
```
  kinovibe: active
  voice_studio: active
  leviathan-daemon: active
  nginx: active

  ✅ Backend FastAPI (порт 8110) — отвечает
  ✅ Flutter Web (порт 8080) — отвечает
  ✅ HTML интерфейс (порт 8081) — отвечает
  ✅ TorrServer (порт 8090) — отвечает

  --- /health ---
  {"status":"ok","version":"5.0.0","service":"KinoVibe Hub"}

  --- /cache/stats ---
  {"entries":11,"hits":1,"misses":18,"hit_rate":0.053,"evictions":0,"ttl_seconds":300,"max_entries":200}
```
## 2. HTML фронтенд
```
  Файл: /var/www/kinovibe/frontend/index.html — НАЙДЕН
  ✅ readonly — не найден
  ✅ Захардкоженных value — не найдено
  BASE_URL найдены: 
  ❌ torrent_player.html — НЕ найден в коде
  ⚠️  Rutube — упоминаний нет
```
## 3. torrent.py
```
  Файл найден: /var/www/kinovibe/backend/providers/torrent.py
  ✅ Кинозал — парсер присутствует
  ⚠️  apibay.org — используется (риск пустых ответов)
  ❌ КРИТИЧНО: найдено условие которое может выбросить результаты Кинозала:
  ✅ Fallback magnet-генерация — найдена
```
## 4. Rutube провайдер
```
  Файл: /var/www/kinovibe/backend/providers/rutube.py
  ❌ embed-ссылки — НЕ формируются (вернёт веб-страницу → белый экран)
  ❌ m3u8 поток — НЕ извлекается
```
## 5. search.py
```
  ✅ фильтр по платформе — ЕСТЬ
  ✅ фильтр популярности — ЕСТЬ
  ✅ режим настроения — ЕСТЬ
  ❌ русский язык в промпте — ОТСУТСТВУЕТ
  ❌ rare/mainstream параметры — ОТСУТСТВУЕТ
```
## 6. Эндпоинты main.py
```
  Все эндпоинты:

  ✅ генерация invite_code — ЕСТЬ
  ✅ invite_url в ответе — ЕСТЬ
  ✅ эндпоинт join — ЕСТЬ
  ✅ cache stats — ЕСТЬ
  ✅ cache clear — ЕСТЬ
```
## 7. Flutter /lib/ экраны
```
  ❌ Директория /var/www/kinovibe/frontend/lib не найдена
```
## 8. Живые тесты API
```
  Тест поиска (все провайдеры):
  ❌ Нет ответа от /search

  Тест создания комнаты Watch Party:
  ❌ invite_url не возвращается: 
```
## ИТОГ

| # | Компонент | Статус | Приоритет |
|---|-----------|--------|-----------|
| 1 | Backend FastAPI :8110 | ❌ сервис упал | КРИТИЧНО |
| 2 | HTML input readonly | ✅ ok | КРИТИЧНО |
| 3 | torrent.py fallback | ✅ ok | КРИТИЧНО |
| 4 | Rutube embed/m3u8 | ❌ белый экран | ВЫСОКИЙ |
| 5 | Фильтр платформ | ✅ ok | ВЫСОКИЙ |
| 6 | Русский язык в поиске | ❌ не добавлен | ВЫСОКИЙ |
| 7 | Watch Party invite_url | ✅ ok | СРЕДНИЙ |
| 8 | Фильтр популярности | ✅ ok | СРЕДНИЙ |

*Отчёт сгенерирован: 2026-05-22 00:25:38*

