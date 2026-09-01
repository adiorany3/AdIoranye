#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Repo Git tidak valid."
  exit 1
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  echo "Remote origin belum ada."
  exit 1
fi

RUN_UPDATE="${RUN_KB_UPDATE_FIRST:-1}"
if [[ "$RUN_UPDATE" == "1" ]]; then
  bash scripts/run_kb_update_local.sh
fi

FILES=(
  ".adioranye_power.db"
  ".adioranye_kb_scrape_state.json"
  ".adioranye_kb_source_health.json"
  "daily_intelligence_briefing.md"
)

if [[ -f "daily_kb_update_report.json" ]]; then
  FILES+=("daily_kb_update_report.json")
fi

changed_files=()
for path in "${FILES[@]}"; do
  if [[ -f "$path" ]]; then
    changed_files+=("$path")
  fi
done

if [[ ${#changed_files[@]} -eq 0 ]]; then
  echo "Tidak ada file KB yang bisa dipublish."
  exit 0
fi

git add -- "${changed_files[@]}"

if git diff --cached --quiet; then
  echo "Tidak ada perubahan KB untuk di-commit."
  exit 0
fi

stamp="$(date '+%Y-%m-%d %H:%M:%S %z')"
commit_message="${KB_GIT_COMMIT_MESSAGE:-chore: update knowledge base ${stamp}}"

git commit -m "$commit_message"
git push origin HEAD

echo "Publish KB selesai."
echo "Files:"
printf ' - %s\n' "${changed_files[@]}"
