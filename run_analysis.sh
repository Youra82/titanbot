#!/bin/bash
# TitanBot run_analysis.sh
# Liest backtest_lookback_weeks und warmup_weeks aus settings.json.
# Berechnet Zeitraum automatisch — kein manuelles Datum nötig.

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

VENV_PATH=".venv/bin/activate"
RESULTS_SCRIPT="src/titanbot/analysis/show_results.py"

if [ ! -f "$VENV_PATH" ]; then
    echo -e "${RED}Fehler: Virtuelle Umgebung nicht gefunden unter '$VENV_PATH'${NC}"
    exit 1
fi

source "$VENV_PATH"

# --- Konfiguration aus settings.json lesen ---
BACKTEST_LOOKBACK_WEEKS=$(python3 -c "
import json
try:
    s = json.load(open('settings.json'))
    print(s.get('optimization_settings', {}).get('backtest_lookback_weeks', 2))
except: print(2)
" 2>/dev/null)

WARMUP_WEEKS=$(python3 -c "
import json
try:
    s = json.load(open('settings.json'))
    print(s.get('optimization_settings', {}).get('warmup_weeks', 4))
except: print(4)
" 2>/dev/null)

START_CAPITAL=$(python3 -c "
import json
try:
    s = json.load(open('settings.json'))
    print(s.get('optimization_settings', {}).get('start_capital', 100))
except: print(100)
" 2>/dev/null)

TARGET_MAX_DD=$(python3 -c "
import json
try:
    s = json.load(open('settings.json'))
    print(s.get('optimization_settings', {}).get('constraints', {}).get('max_drawdown_pct', 30))
except: print(30)
" 2>/dev/null)

# --- Datum automatisch berechnen ---
TODAY=$(date +%Y-%m-%d)

START_DATE=$(python3 -c "
from datetime import datetime, timedelta, timezone
print((datetime.now(timezone.utc) - timedelta(weeks=$BACKTEST_LOOKBACK_WEEKS)).strftime('%Y-%m-%d'))
")

WARMUP_DATE=$(python3 -c "
from datetime import datetime, timedelta, timezone
total = $BACKTEST_LOOKBACK_WEEKS + $WARMUP_WEEKS
print((datetime.now(timezone.utc) - timedelta(weeks=total)).strftime('%Y-%m-%d'))
")

# --- Header ---
echo -e "\n${BLUE}═══════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}             TitanBot Analyse System                   ${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════${NC}"
echo -e "  Backtest-Zeitraum: ${YELLOW}${START_DATE}${NC} → ${YELLOW}${TODAY}${NC}  (${BACKTEST_LOOKBACK_WEEKS} Wochen)"
echo -e "  SMC-Warmup:        ${YELLOW}${WARMUP_DATE}${NC} → ${START_DATE}  (${WARMUP_WEEKS} Wochen extra)"
echo -e "  Startkapital:      ${YELLOW}${START_CAPITAL} USDT${NC}  |  Max DD: ${YELLOW}${TARGET_MAX_DD}%${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════${NC}"
echo -e "  (Zeitraum via ${YELLOW}backtest_lookback_weeks${NC} in settings.json)"
echo -e "${BLUE}═══════════════════════════════════════════════════════${NC}"

# --- Modus-Menü ---
echo -e "\n${YELLOW}Wähle einen Analyse-Modus:${NC}"
echo "  1) Einzel-Analyse      → alle Configs testen → beste auto in settings.json"
echo "  2) Portfolio-Simulation (manuell)"
echo "  3) Portfolio-Optimierung (automatisch) → auto in settings.json"
echo "  4) Interaktive Charts  (SMC + Equity Curve)"

while true; do
    read -p "Auswahl (1-4) [Standard: 1]: " MODE
    MODE=${MODE:-1}
    if [[ "$MODE" =~ ^[1-4]$ ]]; then
        break
    else
        echo -e "${RED}Bitte nur 1, 2, 3 oder 4 eingeben.${NC}"
    fi
done

if [ ! -f "$RESULTS_SCRIPT" ]; then
    echo -e "${RED}Fehler: '$RESULTS_SCRIPT' nicht gefunden.${NC}"
    deactivate
    exit 1
fi

# --- Ausführung ---
if [ "$MODE" == "1" ]; then
    echo -e "\n${BLUE}--- Einzel-Analyse (${BACKTEST_LOOKBACK_WEEKS}W Backtest + ${WARMUP_WEEKS}W SMC-Warmup) ---${NC}"
    python3 "$RESULTS_SCRIPT" \
        --mode 1 \
        --start_date "$START_DATE" \
        --end_date   "$TODAY" \
        --warmup_date "$WARMUP_DATE" \
        --start_capital "$START_CAPITAL" \
        --auto_write

elif [ "$MODE" == "2" ]; then
    echo -e "\n${BLUE}--- Manuelle Portfolio-Simulation (${BACKTEST_LOOKBACK_WEEKS}W Fenster) ---${NC}"
    python3 "$RESULTS_SCRIPT" \
        --mode 2 \
        --start_date "$START_DATE" \
        --end_date   "$TODAY" \
        --warmup_date "$WARMUP_DATE" \
        --start_capital "$START_CAPITAL" \
        --target_max_drawdown "$TARGET_MAX_DD"

elif [ "$MODE" == "3" ]; then
    echo -e "\n${BLUE}--- Auto Portfolio-Optimierung (${BACKTEST_LOOKBACK_WEEKS}W Fenster) → auto in settings.json ---${NC}"
    python3 "$RESULTS_SCRIPT" \
        --mode 3 \
        --start_date "$START_DATE" \
        --end_date   "$TODAY" \
        --warmup_date "$WARMUP_DATE" \
        --start_capital "$START_CAPITAL" \
        --target_max_drawdown "$TARGET_MAX_DD" \
        --auto_write

elif [ "$MODE" == "4" ]; then
    python3 "$RESULTS_SCRIPT" --mode 4
fi

deactivate
