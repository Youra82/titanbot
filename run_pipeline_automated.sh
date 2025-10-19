#!/bin/bash

# --- Pfade und Skripte ---
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
VENV_PATH="$SCRIPT_DIR/.venv/bin/activate"
SETTINGS_FILE="$SCRIPT_DIR/settings.json"
TRAINER="src/jaegerbot/analysis/trainer.py"
OPTIMIZER="src/jaegerbot/analysis/optimizer.py"
CACHE_DIR="$SCRIPT_DIR/data/cache"
TIMESTAMP_FILE="$CACHE_DIR/.last_cleaned"

# --- Umgebung aktivieren ---
source "$VENV_PATH"

echo "--- Starte automatischen Pipeline-Lauf ---"

# --- Python-Helper zum sicheren Auslesen der JSON-Datei ---
get_setting() {
    python3 -c "import json; f=open('$SETTINGS_FILE'); print(json.load(f)$1); f.close()"
}

# --- Automatisches Cache-Management ---
CACHE_DAYS=$(get_setting "['optimization_settings']['auto_clear_cache_days']")

if [ "$CACHE_DAYS" -gt 0 ]; then
    mkdir -p "$CACHE_DIR"
    if [ ! -f "$TIMESTAMP_FILE" ]; then touch "$TIMESTAMP_FILE"; fi
    if [ -n "$(find "$TIMESTAMP_FILE" -mtime +$((CACHE_DAYS - 1)))" ]; then
        echo "Cache ist älter als $CACHE_DAYS Tage. Leere den Cache..."
        rm -rf "$CACHE_DIR"/*
        touch "$TIMESTAMP_FILE"
    else
        echo "Cache ist aktuell. Keine Reinigung notwendig."
    fi
fi

# Lese die restlichen Einstellungen für die Befehlszeile
ENABLED=$(get_setting "['optimization_settings']['enabled']")

if [ "$ENABLED" != "True" ]; then
    echo "Automatische Optimierung ist in settings.json deaktiviert. Breche ab."
    deactivate
    exit 0
fi

SYMBOLS=$(get_setting "['optimization_settings']['symbols_to_optimize']" | tr -d "[]',\"")
TIMEFRAMES=$(get_setting "['optimization_settings']['timeframes_to_optimize']" | tr -d "[]',\"")
LOOKBACK_DAYS=$(get_setting "['optimization_settings']['lookback_days']")
START_CAPITAL=$(get_setting "['optimization_settings']['start_capital']")
N_CORES=$(get_setting "['optimization_settings']['cpu_cores']")
N_TRIALS=$(get_setting "['optimization_settings']['num_trials']")
MAX_DD=$(get_setting "['optimization_settings']['constraints']['max_drawdown_pct']")
MIN_WR=$(get_setting "['optimization_settings']['constraints']['min_win_rate_pct']")
MIN_PNL=$(get_setting "['optimization_settings']['constraints']['min_pnl_pct']")
START_DATE=$(date -d "$LOOKBACK_DAYS days ago" +%F)
END_DATE=$(date +%F)
OPTIM_MODE="strict"
TOP_N=$(get_setting "['live_trading_settings']['top_n_strategies_to_trade']")

# --- Pipeline starten mit sauberen Argumenten ---
echo "Optimierung ist aktiviert. Starte Prozesse..."
echo "Verwende Daten der letzten $LOOKBACK_DAYS Tage."

echo ">>> STUFE 1/2: Starte finales Modelltraining..."
python3 "$TRAINER" \
    --symbols "$SYMBOLS" \
    --timeframes "$TIMEFRAMES" \
    --start_date "$START_DATE" \
    --end_date "$END_DATE"

if [ $? -ne 0 ]; then
    echo "Fehler im Trainer-Skript. Pipeline wird abgebrochen."
    deactivate
    exit 1
fi

echo ">>> STUFE 2/2: Starte Handelsparameter-Optimierung..."
python3 "$OPTIMIZER" \
    --symbols "$SYMBOLS" \
    --timeframes "$TIMEFRAMES" \
    --start_date "$START_DATE" \
    --end_date "$END_DATE" \
    --jobs "$N_CORES" \
    --max_drawdown "$MAX_DD" \
    --start_capital "$START_CAPITAL" \
    --min_win_rate "$MIN_WR" \
    --trials "$N_TRIALS" \
    --min_pnl "$MIN_PNL" \
    --mode "$OPTIM_MODE" \
    --top_n "$TOP_N"

if [ $? -ne 0 ]; then
    echo "Fehler im Optimierer-Skript. Pipeline wird abgebrochen."
    deactivate
    exit 1
fi

deactivate
echo "--- Automatischer Pipeline-Lauf abgeschlossen ---"
