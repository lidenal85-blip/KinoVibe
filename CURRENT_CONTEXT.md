# KinoVibe - CURRENT_CONTEXT.md
# Читай ЭТОТ ФАЙЛ В НАЧАЛЕ КАЖДОЙ СЕССИИ ПО КИНОВАЙБУ

## Статус проекта
- Сервис: active, port 8110, v5.0.0
- Репо: github.com/lidenal85-blip/KinoVibe (branch: master)
- Путь: /var/www/kinovibe/
- Фронт: Flutter Web (/var/www/kinovibe/frontend/build/web)
- Бэкенд: FastAPI /var/www/kinovibe/backend/main.py (770 строк)

## ЧТО РАБОТАЕТ
- YouTube: работает, HLS стриминг ~25сек запуск
- Kodik: работает (аниме/сериалы лицензированные)
- AI поиск: Gemini реформулирует запрос (мастер-промт в search.py)
- VK OAuth: реализован (vk_auth.py + /api/vk/*) — но кнопка входа не показана на главной

## ЧТО НЕ РАБОТАЕТ (баги)
1. ТОРРЕНТЫ: «Во все тяжкие» = 0 результатов. Rutracker/YTS провайдеры сломаны.
2. HDRezka: 403 (подозрительная активнось) — IP сервера заблокирован.
3. VK: кнопка «Войти через VK» не видна на главной странице.
4. HLS: нельзя перемотать/сикать, зависает. Сегменты генерятся только с нуля.
5. UX: вкладки на главной перемешаны, нет логики.
6. Нет раздела Развлечения

## ЧТО ДЕЛАЕМ СЕЙЧАС (сессия 2026-05-28)
1. HLS ускорен: сегменты 2 сек, кеш URL yt-dlp (4h), скопирование stream без перекодирования
2. /home: добавлены секции series
3. TMDB карточки автопоиск /film/streams
4. VK login через VKID SDK
5. Билд Flutter - ожидаем завершения (task d3245b80)
1. VK кнопка входа на главной (перекомпиль фронт)
2. AI-реформулировка запроса: улучшить промт Gemini (уже есть в search.py)
3. Сообщение об ошибке если нет ключей/результатов
4. Торренты разобраться

## ВАЖНО: КАК СОБРАТЬ КОНТЕКСТ
Начало сессии по KinoVibe:
1. Прочитай /var/www/kinovibe/CURRENT_CONTEXT.md (этот файл)
2. Прочитай /var/www/kinovibe/PLAN_FIXES.md
3. git log --oneline -5 в /var/www/kinovibe

## ФАЙЛЫ КОТОРЫЕ ВАЖНЫ
- backend/main.py (770 стр)
- backend/search.py (поиск + Gemini реформулировка)
- backend/vk_auth.py (есть вся логика, нет UI)
- src/lib/screens/hub_screen.dart (главный экран Flutter)
- src/lib/screens/watch_screen.dart (плеер)
- src/lib/services/api_service.dart
- backend/providers/ (hdrezka, kodik, vk, youtube, torrent, rutracker...)
