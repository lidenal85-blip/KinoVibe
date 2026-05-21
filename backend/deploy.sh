#!/bin/bash
# deploy.sh — Быстрый деплой KinoVibe на Yandex Cloud VM
# Использование: ./deploy.sh <PUBLIC_IP> [SSH_KEY_PATH]

set -e

PUBLIC_IP="${1:?Usage: ./deploy.sh <PUBLIC_IP> [SSH_KEY_PATH]}"
SSH_KEY="${2:-~/.ssh/id_rsa}"
REMOTE_DIR="~/kinovibe/server"

echo ">> KinoVibe Deploy >> target: ubuntu@${PUBLIC_IP}"
echo ">> SSH key: ${SSH_KEY}"

# 1. Создать папку на VM
ssh -i "${SSH_KEY}" -o StrictHostKeyChecking=no \
    "ubuntu@${PUBLIC_IP}" "mkdir -p ${REMOTE_DIR}"

# 2. Копировать файлы сервера
echo ">> Копирование файлов сервера..."
scp -i "${SSH_KEY}" -r server/ "ubuntu@${PUBLIC_IP}:~/kinovibe/"

# 3. Установить Docker (если нет)
echo ">> Проверка Docker..."
ssh -i "${SSH_KEY}" "ubuntu@${PUBLIC_IP}" bash <<'REMOTE'
if ! command -v docker &> /dev/null; then
    echo ">> Установка Docker..."
    sudo apt-get update -q
    sudo apt-get install -y docker.io docker-compose
    sudo systemctl enable docker
    sudo systemctl start docker
    sudo usermod -aG docker ubuntu
    echo ">> Docker установлен"
else
    echo ">> Docker уже установлен: $(docker --version)"
fi
REMOTE

# 4. Собрать и запустить контейнер
echo ">> Сборка и запуск контейнера..."
ssh -i "${SSH_KEY}" "ubuntu@${PUBLIC_IP}" bash <<REMOTE
cd ~/kinovibe/server
docker build -t kinovibe-api . --quiet
docker stop kinovibe 2>/dev/null || true
docker rm kinovibe 2>/dev/null || true
docker run -d \
    --name kinovibe \
    -p 8000:8000 \
    --restart always \
    kinovibe-api
echo ">> Контейнер запущен"
REMOTE

# 5. Проверка
echo ">> Проверка /health..."
sleep 3
curl -s "http://${PUBLIC_IP}:8000/health" && echo ""
echo ""
echo "✅ Деплой завершён. API: http://${PUBLIC_IP}:8000"
