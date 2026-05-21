set -euo pipefail

PROJECT="/var/www/kinovibe/frontend"
LOG="/var/www/kinovibe/logs/frontend_build.log"
TEMP="/tmp/kv_web_$(date +%s)"

echo "" >> "$LOG"
echo "[=== BUILD START $(date) ===]" >> "$LOG"

cd "$PROJECT"

echo "[1/5] flutter clean..." | tee -a "$LOG"
flutter clean >> "$LOG" 2>&1

echo "[2/5] flutter pub get..." | tee -a "$LOG"
flutter pub get >> "$LOG" 2>&1

echo "[3/5] flutter build web (canvaskit)..." | tee -a "$LOG"
flutter build web --release --web-renderer canvaskit >> "$LOG" 2>&1

echo "[4/5] Staging..." | tee -a "$LOG"
cp -r "$PROJECT/build/web" "$TEMP"

echo "[5/5] Deploy + cleanup sources..." | tee -a "$LOG"
find "$PROJECT" -mindepth 1 -maxdepth 1   ! -name 'build'   ! -name 'assets'   -exec rm -rf {} + >> "$LOG" 2>&1

cp -r "$TEMP/." "$PROJECT/"
rm -rf "$TEMP"

echo "[nginx] restarting..." | tee -a "$LOG"
systemctl restart nginx

echo "[=== DEPLOY COMPLETE $(date) ===]" | tee -a "$LOG"
