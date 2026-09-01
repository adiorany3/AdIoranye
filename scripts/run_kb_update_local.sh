#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

python daily_kb_scraper.py \
  --db "${POWER_DB_PATH:-.adioranye_power.db}" \
  --sources "${KB_SCRAPER_SOURCES_FILE:-kb_sources.json}" \
  --state "${KB_SCRAPER_STATE_FILE:-.adioranye_kb_scrape_state.json}" \
  --watchlist "${CRITICAL_WATCHLIST_FILE:-critical_watchlist.json}" \
  --briefing-file "${DAILY_INTELLIGENCE_BRIEFING_FILE:-daily_intelligence_briefing.md}" \
  --backup-dir "${DB_BACKUP_DIR:-.db_backups}" \
  --max-backups "${DB_BACKUP_MAX_COUNT:-10}" \
  --max-items "${KB_SCRAPER_MAX_ITEMS_PER_SOURCE:-5}" \
  --timeout "${KB_SCRAPER_TIMEOUT:-20}" \
  --time-budget-seconds "${KB_UPDATE_TIME_BUDGET_SECONDS:-0}" \
  --source-limit "${KB_SCRAPER_SOURCE_LIMIT:-0}" \
  ${KB_SCRAPER_FORCE:+--force} \
  ${KB_SCRAPER_DRY_RUN:+--dry-run} \
  ${KB_SCRAPER_SKIP_DB_BACKUP:+--skip-db-backup} \
  ${KB_SCRAPER_NO_SOURCE_ROTATION:+--no-source-rotation} \
  ${KB_SCRAPER_REPORT_FILE:+--report-file "$KB_SCRAPER_REPORT_FILE"} \
  ${KB_SCRAPER_QUIET:+--quiet}
