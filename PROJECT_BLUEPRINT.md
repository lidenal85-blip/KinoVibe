# KinoVibe v5.0 — PROJECT BLUEPRINT
**Codename: Abyssal Leviathan**  
**Зафиксировано: 2026-05-21**

---

## Обзор

KinoVibe — Flutter Web-приложение с Python (FastAPI) бэкендом. Агрегатор стриминга с поддержкой нескольких провайдеров, поиском, просмотром и Watch Party через WebSocket.

Деплой: `kinovibe.leviathanstory.ru` / `78.17.24.96`  
Стек: Flutter 3.x (Web) + FastAPI (Python) + Nginx + Uvicorn

---

## Структура репозитория

```
/var/www/kinovibe/
├── src/                        # Flutter-приложение (исходники)
│   ├── pubspec.yaml
│   └── lib/
│       ├── main.dart
│       ├── models/movie.dart
│       ├── screens/
│       │   ├── hub_screen.dart
│       │   └── watch_screen.dart
│       ├── services/api_service.dart
│       ├── theme/abyssal_theme.dart
│       └── widgets/
│           ├── movie_card.dart
│           ├── search_bar_widget.dart
│           └── category_chips.dart
├── backend/                    # FastAPI-сервер (порт 8110)
│   ├── main.py                 # API v4.1: /search, /stream, /rooms, /ws
│   ├── search.py
│   ├── signaling.py            # WebSocket signaling для комнат
│   ├── key_pool.py             # Пул API-ключей
│   ├── meta_agent.py
│   ├── providers/              # Провайдеры: youtube, vk, kodik, torrent, hdrezka
│   └── requirements.txt
├── frontend/                   # Скомпилированный Flutter Web (деплоится)
│   └── build/web/
├── nginx/
│   ├── kinovibe                # Конфиг для домена
│   └── kinovibe-ip             # Конфиг для IP
└── PROJECT_BLUEPRINT.md        # Этот файл
```

---

## Flutter: Архитектура фронтенда

### Точка входа — `main.dart`
- Класс `KinoVibeApp` — MaterialApp
- Тема: `AbyssalTheme.theme`
- Домашний экран: `HubScreen`
- Название: `KinoVibe v5.0`

### Модели — `models/movie.dart`

| Класс | Назначение |
|-------|-----------|
| `Movie` | Карточка контента: title, poster, year, rating, description, url, provider, category |
| `SearchResult` | Ответ поиска: items (List\<Movie\>), error, total |
| `Room` | Watch Party комната: id, movieTitle, movieUrl, peers |

### Сервис — `services/api_service.dart`

Базовый URL: `http://localhost/api` (через nginx-прокси)

| Метод | Эндпоинт | Описание |
|-------|----------|----------|
| `search(query, category)` | `POST /api/search` | Полнотекстовый поиск |
| `getStream(url, provider)` | `GET /api/stream` | Получить stream URL |
| `createRoom(movieUrl, movieTitle)` | `POST /api/rooms/create` | Создать Watch Party |
| `listRooms()` | `GET /api/rooms` | Список активных комнат |

Таймауты: search 30с, stream 30с, rooms 10с.

### Экраны

#### `HubScreen` — главный хаб
- `SliverAppBar` (floating+snap) с логотипом Leviathan и индикатором ONLINE
- `AbyssalSearchBar` — поисковая строка с пульсирующим glow при фокусе
- `CategoryChips` — фильтр: Фильмы / Сериалы / Аниме / Мультики
- `SliverGrid` с результатами (2/4/6 колонок в зависимости от ширины экрана)
- Состояния: WelcomeState → Loading (skeleton) → Results / EmptyState / ErrorBanner
- Hover-навигация на `WatchScreen` + создание Watch Party через `ApiService`

**Breakpoints:**
- `< 600px` → 2 колонки
- `600–900px` → 4 колонки
- `> 900px` → 6 колонок, горизонтальные паддинги 48px

#### `WatchScreen` — плеер + инфо + чат
- AppBar с названием фильма и кнопкой назад
- `_PlayerArea`: загружает stream, показывает плейсхолдер (встроенный плеер — в разработке)
- Wide layout (>900px): `_MovieInfo` слева (flex 2) + `_ChatPanel` справа (flex 1)
- Mobile layout: TabBar "Инфо" / "Чат"
- `_ChatPanel`: локальный чат (не подключён к WS), готов к интеграции

### Виджеты

#### `MovieCard`
- Hover-анимация (200ms): border, цвет фона, cyan glow, кнопка play по центру
- `_PosterSection`: CachedNetworkImage + shimmer-заглушка + gradient overlay
- Badges: провайдер (цвет по: youtube=red, vk=blue, torrent=green, kodik=violet) + рейтинг
- `_InfoSection`: заголовок, год, кнопка "Смотреть" + кнопка "Вечеринка" (violet)

#### `AbyssalSearchBar`
- AnimationController: пульсирующий glow при фокусе (2с, Tween 0.3→0.7)
- Иконка поиска (cyan), spinner при загрузке, крестик для очистки

#### `CategoryChips`
- 4 категории: movies, series, anime, cartoons
- ScaleTransition (0.95→1.0, 150ms) + AnimatedContainer (200ms)
- Активный чип: cyan glow, cyan border, жирный текст

---

## Тема — Abyssal Leviathan

Файл: `theme/abyssal_theme.dart`  
Шрифт: **Nunito** (Google Fonts), Material3, dark mode

### Цветовая палитра

| Переменная | HEX | Назначение |
|-----------|-----|-----------|
| `background` | `#050A14` | Фон страницы |
| `surface` | `#0A1628` | AppBar, карточки |
| `card` | `#0D1F3C` | Карточки |
| `cardHover` | `#122540` | Hover-состояние |
| `cyan` | `#00D4FF` | Основной акцент, кнопки, иконки |
| `cyanDim` | `#0099BB` | Приглушённый cyan |
| `violet` | `#7B2FBE` | Вторичный акцент (Watch Party) |
| `violetGlow` | `#9B4FDE` | Glow для violet |
| `textPrimary` | `#E8F4FD` | Основной текст |
| `textSecondary` | `#6B8CAE` | Второстепенный текст |
| `textMuted` | `#3A5A7C` | Заглушённый текст |
| `borderSubtle` | `#26007799` | Рамки (cyan 15%) |
| `borderActive` | `#66007799` | Активные рамки (cyan 40%) |
| `success` | `#00E5A0` | Успех (ONLINE индикатор) |
| `error` | `#FF3B5C` | Ошибки |
| `warning` | `#FFB020` | Рейтинг (звёзды) |

Glow-эффекты: `cyanGlow(intensity)` — двойной BoxShadow (15%×intensity + 8%×intensity).

---

## Бэкенд — FastAPI v4.1

Файл: `backend/main.py`  
Порт: **8110** (локально, за nginx)

### Провайдеры контента
- `youtube.py` — YouTube
- `vk.py` — VK Video
- `kodik.py` — Kodik (аниме/сериалы)
- `torrent.py` — торренты
- `hdrezka.py` — HDRezka

### Ключевые компоненты
- `search.py` — `execute_search()` — мультипровайдерный поиск
- `signaling.py` — WebSocket signaling для Watch Party комнат
- `key_pool.py` — пул API-ключей (также `/opt/leviathan_engine/core/key_pool.py`)
- `meta_agent.py` — мета-агент (AI-слой)
- `vault.json` — хранилище ключей

### API-эндпоинты
| Метод | URL | Описание |
|-------|-----|----------|
| `GET` | `/health` | Статус сервера |
| `GET` | `/pool/status` | Статус пула ключей |
| `POST` | `/search` | Поиск контента |
| `GET` | `/stream` | Получить stream URL |
| `POST` | `/rooms/create` | Создать комнату |
| `GET` | `/rooms` | Список комнат |
| `WS` | `/ws/{room_id}` | WebSocket signaling |

---

## Nginx

Файл: `nginx/kinovibe`  
Домены: `kinovibe.leviathanstory.ru`, `78.17.24.96`  
Веб-корень: `/var/www/kinovibe/frontend/build/web`

| Location | Назначение |
|---------|-----------|
| `/api/ws/` | Прокси WebSocket → `127.0.0.1:8110/ws/` (таймаут 3600с) |
| `/api/` | Прокси REST → `127.0.0.1:8110/` (таймаут 60с) |
| `/` | Flutter SPA, fallback → `/index.html` |

---

## Flutter-зависимости (pubspec.yaml)

| Пакет | Версия | Назначение |
|-------|--------|-----------|
| `http` | ^1.2.0 | REST-запросы |
| `web_socket_channel` | ^3.0.0 | WebSocket (Watch Party) |
| `google_fonts` | ^6.2.1 | Шрифт Nunito |
| `cached_network_image` | ^3.3.1 | Кэшированные постеры |
| `shimmer` | ^3.0.0 | Skeleton-анимация загрузки |

---

## Что сделано / Что не готово

### Готово
- [x] Тема Abyssal Leviathan (цвета, шрифт, компоненты)
- [x] HubScreen: поиск, категории, сетка результатов, состояния
- [x] WatchScreen: скелет плеера, MovieInfo, ChatPanel UI
- [x] MovieCard: hover-анимации, badges, shimmer
- [x] ApiService: все методы (search, stream, rooms)
- [x] Модели: Movie, SearchResult, Room
- [x] Nginx: прокси для API и WebSocket
- [x] Бэкенд: мультипровайдерный поиск, signaling

### В разработке / Не реализовано
- [ ] Встроенный видеоплеер (сейчас — плейсхолдер с stream URL)
- [ ] WebSocket-интеграция чата (сейчас — локальный стейт)
- [ ] Watch Party: синхронизация воспроизведения по WS
- [ ] Аутентификация пользователей
- [ ] Сохранение истории просмотра
- [ ] Комнаты: отображение списка, присоединение по ID
