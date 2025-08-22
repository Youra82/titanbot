#!/bin/bash

# --- Dynamische Pfadermittlung ---
# Stellt sicher, dass das Skript von überall aus funktioniert.
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)

# Pfade zu wichtigen Dateien und Verzeichnissen
CONFIG_FILE="$SCRIPT_DIR/code/strategies/envelope/config.json"
LOG_FILE="$SCRIPT_DIR/logs/titanbot.log"
OPTIMIZER_SCRIPT="$SCRIPT_DIR/code/analysis/optimizer.py"
CACHE_DIR="$SCRIPT_DIR/code/analysis/historical_data"

# --- Farbcodes für eine schönere Ausgabe ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# --- Funktion für den Optimizer ---
function run_optimizer() {
    echo -e "${CYAN}=======================================================${NC}"
    echo -e "${CYAN}            TITANBOT - STRATEGIE-OPTIMIZER             ${NC}"
    echo -e "${CYAN}=======================================================${NC}"
    read -p "Startdatum (YYYY-MM-DD): " START_DATE
    read -p "Enddatum (YYYY-MM-DD): " END_DATE
    # +++ HIER IST DIE GEWÜNSCHTE ÄNDERUNG +++
    read -p "Handelspaar (z.B. BTC ETH): " SYMBOL
    read -p "Maximaler Hebel für Simulation (z.B. 10): " LEVERAGE
    read -p "Startkapital in USDT (z.B. 1000): " START_CAPITAL
    read -p "Margin pro Trade in % (z.B. 10): " TRADE_SIZE_PCT

    if [ -z "$START_DATE" ] || [ -z "$END_DATE" ] || [ -z "$SYMBOL" ] || [ -z "$LEVERAGE" ] || [ -z "$START_CAPITAL" ] || [ -z "$TRADE_SIZE_PCT" ]; then
        echo -e "${RED}Fehler: Alle Felder müssen ausgefüllt werden.${NC}"; exit 1;
    fi

    # Aktiviere die virtuelle Umgebung und starte den Optimizer
    source "$SCRIPT_DIR/code/.venv/bin/activate"

    python3 "$OPTIMIZER_SCRIPT" \
        --start "$START_DATE" \
        --end "$END_DATE" \
        --symbol "$SYMBOL" \
        --leverage "$LEVERAGE" \
        --start_capital "$START_CAPITAL" \
        --trade_size_pct "$TRADE_SIZE_PCT"

    echo -e "\n${GREEN}Optimierungslauf abgeschlossen.${NC}"
}

# --- MODUS-AUSWAHL ---
case "$1" in
    optimize)
        run_optimizer
        exit 0
        ;;
    clear-cache)
        read -p "Möchtest du den gesamten Daten-Cache löschen? [j/N]: " response
        if [[ "$response" =~ ^([jJ][aA]|[jJ])$ ]]; then
            rm -rf "$CACHE_DIR" && echo -e "${GREEN}✔ Cache wurde erfolgreich gelöscht.${NC}"
        else
            echo -e "${RED}Aktion abgebrochen.${NC}"
        fi
        exit 0
        ;;
esac

# ######################################################################
# ### STANDARD-MONITORING-ANSICHT ###
# ######################################################################
echo -e "${CYAN}=======================================================${NC}"
echo -e "${CYAN}             TITAN TRADING BOT MONITORING              ${NC}"
echo -e "${CYAN}=======================================================${NC}"
echo "Verwende './monitor_titanbot.sh <mode>', Modi: ${GREEN}optimize, clear-cache${NC}"
echo -e "Letzte Aktualisierung: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# --- Konfiguration & Strategie ---
echo -e "${YELLOW}--- KONFIGURATION ---${NC}"
if [ -f "$CONFIG_FILE" ]; then
    if command -v jq &> /dev/null; then
        SYMBOL=$(jq -r '._HEADING_STEP_3_.global_settings.symbol' "$CONFIG_FILE")
        LEVERAGE=$(jq -r '._HEADING_STEP_3_.global_settings.leverage' "$CONFIG_FILE")
        STRATEGY_NUM=$(jq -r '._HEADING_STEP_1_.active_strategy_number' "$CONFIG_FILE")
        STRATEGY_NAME=$(jq -r "._HEADING_STEP_1_.strategy_map[\"$STRATEGY_NUM\"]" "$CONFIG_FILE")
        
        echo "Handelspaar: $SYMBOL, Hebel: ${LEVERAGE}x"
        echo -e "Aktive Strategie: ${GREEN}$STRATEGY_NAME${NC}"
    else
        echo -e "${RED}Fehler: 'jq' ist nicht installiert. Bitte mit 'sudo apt install jq' nachholen.${NC}"
    fi
else
    echo -e "${RED}Fehler: Konfigurationsdatei nicht gefunden unter $CONFIG_FILE${NC}"
fi
echo ""

# --- Bot-Status aus Log ---
echo -e "${YELLOW}--- AKTUELLER STATUS & LETZTE AKTIVITÄT ---${NC}"
if [ -f "$LOG_FILE" ]; then
    # Zeige die letzten 5 relevanten Zeilen aus dem Log
    echo "Letzte Log-Einträge:"
    grep -v "^\s*$" "$LOG_FILE" | tail -n 5
    
    echo ""
    ERROR_COUNT=$(grep -c -iE "Fehler|error|fatal" "$LOG_FILE")
    if [ "$ERROR_COUNT" -gt 0 ]; then
        echo -e "Fehlerzähler: ${RED}${ERROR_COUNT} Fehler protokolliert${NC}"
    else
        echo -e "Fehlerzähler: ${GREEN}Keine Fehler im Log gefunden${NC}"
    fi
else
    echo "Log-Datei ($LOG_FILE) noch nicht vorhanden."
fi
echo -e "${CYAN}=======================================================${NC}"
