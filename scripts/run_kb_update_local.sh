#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  PYTHON_BIN="$(command -v python)"
fi

"$PYTHON_BIN" -m pip install --quiet "requests>=2.31" >/dev/null 2>&1 || true

EFFECTIVE_SOURCES_FILE="${KB_EFFECTIVE_SOURCES_FILE:-kb_sources_effective.json}"
OPTIMIZER_HEALTH_FILE="${KB_SOURCE_HEALTH_FILE:-.adioranye_kb_source_health.json}"
OPTIMIZER_REPORT_FILE="${KB_RUNTIME_OPTIMIZER_REPORT_FILE:-kb_runtime_optimizer_prepare.json}"
SCRAPER_SOURCES_FILE="${KB_SCRAPER_SOURCES_FILE:-kb_sources.json}"
SCRAPER_REPORT_FILE="${KB_SCRAPER_REPORT_FILE:-daily_kb_update_report.json}"
UPDATE_PROFILE="${KB_UPDATE_PROFILE:-all}"
UPDATE_SOURCE_LIMIT="${KB_UPDATE_SOURCE_LIMIT:-${KB_SCRAPER_SOURCE_LIMIT:-0}}"
UPDATE_MAX_ITEMS="${KB_UPDATE_MAX_ITEMS:-${KB_SCRAPER_MAX_ITEMS_PER_SOURCE:-5}}"

# Default full local KB. Optimizer remains optional for deliberately limited runs.
if [[ "${KB_USE_RUNTIME_OPTIMIZER:-0}" == "1" ]]; then
  "$PYTHON_BIN" kb_runtime_optimizer.py prepare \
    --sources "$SCRAPER_SOURCES_FILE" \
    --health "$OPTIMIZER_HEALTH_FILE" \
    --output "$EFFECTIVE_SOURCES_FILE" \
    --report "$OPTIMIZER_REPORT_FILE" \
    --source-limit "$UPDATE_SOURCE_LIMIT" \
    --max-items "$UPDATE_MAX_ITEMS" \
    --profile "$UPDATE_PROFILE"
  SCRAPER_SOURCES_FILE="$EFFECTIVE_SOURCES_FILE"
fi

set +e
"$PYTHON_BIN" daily_kb_scraper.py \
  --db "${POWER_DB_PATH:-.adioranye_power.db}" \
  --sources "$SCRAPER_SOURCES_FILE" \
  --state "${KB_SCRAPER_STATE_FILE:-.adioranye_kb_scrape_state.json}" \
  --watchlist "${CRITICAL_WATCHLIST_FILE:-critical_watchlist.json}" \
  --briefing-file "${DAILY_INTELLIGENCE_BRIEFING_FILE:-daily_intelligence_briefing.md}" \
  --backup-dir "${DB_BACKUP_DIR:-.db_backups}" \
  --max-backups "${DB_BACKUP_MAX_COUNT:-10}" \
  --max-items "$UPDATE_MAX_ITEMS" \
  --timeout "${KB_SCRAPER_TIMEOUT:-20}" \
  --time-budget-seconds "${KB_UPDATE_TIME_BUDGET_SECONDS:-0}" \
  --source-limit "$UPDATE_SOURCE_LIMIT" \
  --report-file "$SCRAPER_REPORT_FILE" \
  ${KB_SCRAPER_FORCE:+--force} \
  ${KB_SCRAPER_DRY_RUN:+--dry-run} \
  ${KB_SCRAPER_SKIP_DB_BACKUP:+--skip-db-backup} \
  ${KB_SCRAPER_NO_SOURCE_ROTATION:+--no-source-rotation} \
  ${KB_SCRAPER_QUIET:+--quiet}
SCRAPER_EXIT_CODE=$?
set -e

if [[ "${KB_USE_RUNTIME_OPTIMIZER:-0}" == "1" ]]; then
  "$PYTHON_BIN" kb_runtime_optimizer.py update-health \
    --health "$OPTIMIZER_HEALTH_FILE" \
    --effective-sources "$EFFECTIVE_SOURCES_FILE" \
    --scraper-report "$SCRAPER_REPORT_FILE" \
    $( [[ "$SCRAPER_EXIT_CODE" != "0" ]] && printf '%s' '--workflow-failed' )
fi

exit "$SCRAPER_EXIT_CODE"
