#!/bin/bash
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}======================================================="
echo "    JaegerBot Vollautomatische 3-Stufen-Pipeline"
echo -e "=======================================================${NC}"

# --- Pfade definieren ---
VENV_PATH=".venv/bin/activate"
TRAINER="src/jaegerbot/analysis/trainer.py"
THRESHOLD_FINDER="src/jaegerbot/analysis/find_best_threshold.py"
OPTIMIZER="src/jaegerbot/analysis/optimizer.py"

# --- Umgebung aktivieren ---
source "$VENV_PATH"
echo -e "${GREEN}✔ Virtuelle Umgebung wurde erfolgreich aktiviert.${NC}"

# --- AUFRÄUM-ASSISTENT (SICHERE VERSION) ---
echo -e "\n${YELLOW}Möchtest du alle alten, generierten Ergebnisse (Modelle & Konfigs) vor dem Start löschen?${NC}"
read -p "Dies wird für einen kompletten Neustart empfohlen. (j/n) [Standard: n]: " CLEANUP_CHOICE; CLEANUP_CHOICE=${CLEANUP_CHOICE:-n}
if [[ "$CLEANUP_CHOICE" == "j" || "$CLEANUP_CHOICE" == "J" ]]; then
    echo -e "${YELLOW}Lösche alte Konfigurationen und Modelle...${NC}"; rm -f src/jaegerbot/strategy/configs/config_*.json; rm -f artifacts/models/*; echo -e "${GREEN}✔ Aufräumen abgeschlossen.${NC}"
else
    echo -e "${GREEN}✔ Alte Ergebnisse werden beibehalten.${NC}"
fi

# --- Interaktive Abfrage ---
read -p "Handelspaar(e) eingeben (ohne /USDT, z.B. BTC ETH): " SYMBOLS
read -p "Zeitfenster eingeben (z.B. 1h 4h): " TIMEFRAMES
echo -e "\n${BLUE}--- Empfehlung: Optimaler Rückblick-Zeitraum ---${NC}"
printf "+-------------+--------------------------------+\n"; printf "| Zeitfenster | Empfohlener Rückblick (Tage)   |\n"; printf "+-------------+--------------------------------+\n"; printf "| 5m, 15m     | 15 - 90 Tage                   |\n"; printf "| 30m, 1h     | 180 - 365 Tage                 |\n"; printf "| 2h, 4h      | 550 - 730 Tage                 |\n"; printf "| 6h, 1d      | 1095 - 1825 Tage               |\n"; printf "+-------------+--------------------------------+\n"
read -p "Startdatum (JJJJ-MM-TT) oder 'a' für Automatik [Standard: a]: " START_DATE_INPUT; START_DATE_INPUT=${START_DATE_INPUT:-a}
read -p "Enddatum (JJJJ-MM-TT) [Standard: Heute]: " END_DATE; END_DATE=${END_DATE:-$(date +%F)}
read -p "Startkapital in USDT [Standard: 1000]: " START_CAPITAL; START_CAPITAL=${START_CAPITAL:-1000}
read -p "CPU-Kerne [Standard: -1 für alle]: " N_CORES; N_CORES=${N_CORES:--1}
read -p "Anzahl Trials [Standard: 200]: " N_TRIALS; N_TRIALS=${N_TRIALS:-200}
read -p "Mindest-Genauigkeit in % eingeben [Standard: 55]: " MIN_ACCURACY; MIN_ACCURACY=${MIN_ACCURACY:-55}

echo -e "\n${YELLOW}Wähle den MACD-Filter-Modus:${NC}"
echo "  1) Nur MIT MACD-Filter optimieren"
echo "  2) Nur OHNE MACD-Filter optimieren"
echo "  3) BEIDES optimieren (erstellt separate Configs)"
read -p "Auswahl (1-3) [Standard: 1]: " MACD_MODE; MACD_MODE=${MACD_MODE:-1}

echo -e "\n${YELLOW}Wähle einen Optimierungs-Modus:${NC}"; echo "  1) Strenger Modus"; echo "  2) 'Finde das Beste'-Modus"
read -p "Auswahl (1-2) [Standard: 1]: " OPTIM_MODE; OPTIM_MODE=${OPTIM_MODE:-1}
if [ "$OPTIM_MODE" == "1" ]; then
    OPTIM_MODE_ARG="strict"; read -p "Max Drawdown % [Standard: 30]: " MAX_DD; MAX_DD=${MAX_DD:-30}; read -p "Min Win-Rate % [Standard: 55]: " MIN_WR; MIN_WR=${MIN_WR:-55}; read -p "Min PnL % [Standard: 0]: " MIN_PNL; MIN_PNL=${MIN_PNL:-0}
else
    OPTIM_MODE_ARG="best_profit"; read -p "Max Drawdown % [Standard: 30]: " MAX_DD; MAX_DD=${MAX_DD:-30}; MIN_WR=0; MIN_PNL=-99999
fi

run_optimization() {
    local symbol=$1; local timeframe=$2; local use_macd=$3; local suffix=$4
    echo -e "\n${GREEN}>>> STUFE 3/3: Starte Optimierung für $symbol ($timeframe) ${suffix}...${NC}"
    python3 "$OPTIMIZER" --symbols "$symbol" --timeframes "$timeframe" --start_date "$CURRENT_START_DATE" --end_date "$CURRENT_END_DATE" --jobs "$N_CORES" --max_drawdown "$MAX_DD" --start_capital "$START_CAPITAL" --min_win_rate "$MIN_WR" --trials "$N_TRIALS" --min_pnl "$MIN_PNL" --mode "$OPTIM_MODE_ARG" --threshold "$BEST_THRESHOLD" --use_macd_filter "$use_macd" --config_suffix "$suffix"
    if [ $? -ne 0 ]; then echo -e "${RED}Fehler im Optimierer für $symbol ($timeframe). Überspringe...${NC}"; fi
}

for symbol in $SYMBOLS; do
    for timeframe in $TIMEFRAMES; do
        pipeline_success=false
        for i in {1..3}; do
            if [ "$START_DATE_INPUT" == "a" ]; then
                lookback_days=365; case "$timeframe" in 5m|15m) lookback_days=60 ;; 30m|1h) lookback_days=365 ;; 2h|4h) lookback_days=730 ;; 6h|1d) lookback_days=1095 ;; esac
                start_year_offset=$(( (i - 1) * 365 )); total_offset=$(( lookback_days + start_year_offset ))
                CURRENT_START_DATE=$(date -d "$total_offset days ago" +%F); CURRENT_END_DATE=$(date -d "$start_year_offset days ago" +%F)
            else
                year_offset=$(( i - 1 )); CURRENT_START_DATE=$(date -d "$START_DATE_INPUT -$year_offset year" +%F); CURRENT_END_DATE=$(date -d "$END_DATE -$year_offset year" +%F)
            fi
            echo -e "\n${BLUE}=======================================================${NC}"; echo -e "${BLUE}  Bearbeite Pipeline für: $symbol ($timeframe) - VERSUCH $i/3${NC}"; echo -e "${BLUE}  Datenzeitraum: $CURRENT_START_DATE bis $CURRENT_END_DATE${NC}"; echo -e "${BLUE}=======================================================${NC}"
            
            echo -e "\n${GREEN}>>> STUFE 1/3: Starte Modelltraining...${NC}"; TRAINER_OUTPUT=$(python3 "$TRAINER" --symbols "$symbol" --timeframes "$timeframe" --start_date "$CURRENT_START_DATE" --end_date "$END_DATE" 2>&1); echo "$TRAINER_OUTPUT"
            MODEL_ACCURACY=$(echo "$TRAINER_OUTPUT" | awk '/Modell-Genauigkeit auf Testdaten:/ {gsub(/%/, ""); print $NF}');
            if [[ -z "$MODEL_ACCURACY" ]] || ! (( $(echo "$MODEL_ACCURACY >= $MIN_ACCURACY" | bc -l) )); then echo -e "${YELLOW}Versuch $i nicht erfolgreich (Modell-Qualität unzureichend).${NC}"; continue; fi
            echo -e "${GREEN}✔ Qualitätscheck bestanden (${MODEL_ACCURACY}%).${NC}"

            echo -e "\n${GREEN}>>> STUFE 2/3: Suche besten Threshold...${NC}"; THRESHOLD_OUTPUT=$(python3 "$THRESHOLD_FINDER" --symbol "$symbol" --timeframe "$timeframe" --start_date "$CURRENT_START_DATE" --end_date "$END_DATE"); BEST_THRESHOLD=$(echo "$THRESHOLD_OUTPUT" | tail -n 1)
            if ! [[ "$BEST_THRESHOLD" =~ ^[0-9]+\.[0-9]+$ ]]; then echo -e "${YELLOW}Versuch $i nicht erfolgreich (Kein Threshold gefunden).${NC}"; continue; fi
            echo -e "${GREEN}✔ Bester Threshold auf ${BEST_THRESHOLD} gesetzt.${NC}"
            
            case "$MACD_MODE" in
                1) run_optimization "$symbol" "$timeframe" "j" "_macd" ;;
                2) run_optimization "$symbol" "$timeframe" "n" "" ;;
                3) run_optimization "$symbol" "$timeframe" "j" "_macd"; run_optimization "$symbol" "$timeframe" "n" "" ;;
            esac
            
            pipeline_success=true; break
        done
        if [ "$pipeline_success" = false ]; then echo -e "\n${RED}=======================================================${NC}"; echo -e "${RED}  FINALER FEHLER: Konnte nach 3 Versuchen keine Strategie für $symbol ($timeframe) finden.${NC}"; echo -e "${RED}=======================================================${NC}"; fi
    done
done

deactivate
echo -e "\n${BLUE}✔ Alle Pipeline-Aufgaben erfolgreich abgeschlossen!${NC}"
