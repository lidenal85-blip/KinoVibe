# KinoVibe — Деплой на Yandex Cloud VM

## Требования к VM

| Параметр | Значение |
|----------|----------|
| ОС | Ubuntu 22.04 LTS |
| CPU | 2 vCPU |
| RAM | 4 GB |
| Диск | 20 GB SSD |
| Порт | 8000 (открыт в группе безопасности) |

---

## Шаг 1. Создай VM в Yandex Cloud

1. Console → Compute Cloud → Создать ВМ
2. ОС: Ubuntu 22.04
3. Добавь SSH-ключ (или сгенерируй новый)
4. Запомни публичный IP

---

## Шаг 2. Заполни vault.json

```json
{
  "gemini_keys": ["AIza...", "AIza...", ...],
  "groq_keys": ["gsk_...", ...],
  "vk_token": "optional"
}
```

⚠️ **vault.json не коммитится в git** (.gitignore)

---

## Шаг 3. Быстрый деплой одной командой

```bash
chmod +x server/deploy.sh
./server/deploy.sh <PUBLIC_IP> ~/.ssh/your-key.pem
```

Скрипт сам: скопирует файлы → установит Docker → соберёт образ → запустит контейнер.

---

## Шаг 4. Ручной деплой (если нужен контроль)

```bash
# 1. Подключиться к VM
ssh -i ~/.ssh/your-key.pem ubuntu@<PUBLIC_IP>

# 2. Установить Docker
sudo apt update && sudo apt install -y docker.io
sudo systemctl enable --now docker
sudo usermod -aG docker ubuntu
# Переподключись для применения группы

# 3. На локальной машине — скопировать сервер
scp -i ~/.ssh/your-key.pem -r server/ ubuntu@<PUBLIC_IP>:~/kinovibe/

# 4. На VM — собрать и запустить
cd ~/kinovibe/server
docker build -t kinovibe-api .
docker run -d \
  --name kinovibe \
  -p 8000:8000 \
  --restart always \
  kinovibe-api

# 5. Проверка
curl http://localhost:8000/health
```

---

## Шаг 5. Проверка снаружи

```bash
curl http://<PUBLIC_IP>:8000/health
# → {"status":"ok","version":"3.0.0","service":"KinoVibe"}

curl -X POST http://<PUBLIC_IP>:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query": "грустно, хочу что-то тёплое", "category": "movies"}'
```

---

## Управление контейнером

```bash
# Статус
docker ps

# Логи
docker logs kinovibe -f

# Остановить
docker stop kinovibe

# Перезапустить
docker restart kinovibe

# Обновить (после изменений)
docker stop kinovibe && docker rm kinovibe
docker build -t kinovibe-api .
docker run -d --name kinovibe -p 8000:8000 --restart always kinovibe-api
```

---

## Переключить Flutter на внешний сервер

В `client/lib/services/api_config.dart` замени `<PUBLIC_IP>` на реальный IP:

```dart
static const String remote = 'http://123.45.67.89:8000';
```

Сборка с внешним сервером:
```bash
flutter build apk --release --dart-define=USE_REMOTE=true
```
