#!/usr/bin/env bash
set -euo pipefail
python daily_kb_scraper.py \
  --db "${POWER_DB_PATH:-.adioranye_power.db}" \
  --sources "${KB_SCRAPER_SOURCES_FILE:-kb_sources.json}" \
  --state "${KB_SCRAPER_STATE_FILE:-.adioranye_kb_scrape_state.json}" \
  --max-items "${KB_SCRAPER_MAX_ITEMS_PER_SOURCE:-5}"
