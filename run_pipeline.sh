#!/bin/bash
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}======================================================="
echo "       TitanBot SMC Optimierungs-Pipeline"
echo -e "=======================================================${NC}"

# --- Pfade definieren ---
VENV_PATH=".venv/bin/activate"
OPTIMIZER="src/titanbot/analysis/optimizer.py" 

# --- Umgebung aktivieren ---
source "$VENV_PATH"
echo -e "${GREEN}✔ Virtuelle Umgebung wurde erfolgreich aktiviert.${NC}"

# --- AUFRÄUM-ASSISTENT ---
echo -e "\n${YELLOW}Möchtest du alle alten, generierten Configs vor dem Start löschen?${NC}"
read -p "Dies wird für einen kompletten Neustart empfohlen. (j/n) [Standard: n]: " CLEANUP_CHOICE; CLEANUP_CHOICE=${CLEANUP_CHOICE:-n}
if [[ "$CLEANUP_CHOICE" == "j" || "$CLEANUP_CHOICE" == "J" ]]; then
    echo -e "${YELLOW}Lösche alte Konfigurationen...${NC}"; rm -f src/titanbot/strategy/configs/config_*.json; echo -e "${GREEN}✔ Aufräumen abgeschlossen.${NC}"
else
    echo -e "${GREEN}✔ Alte Ergebnisse werden beibehalten.${NC}"
fi

# --- Interaktive Abfrage ---
read -p "Handelspaar(e) eingeben (ohne /USDT, z.B. BTC ETH): " SYMBOLS
read -p "Zeitfenster eingeben (z.B. 1h 4h): " TIMEFRAMES

# *** HIER IST DIE WIEDER EINGEFÜGTE TABELLE UND DATUMSLOGIK ***
echo -e "\n${BLUE}--- Empfehlung: Optimaler Rückblick-Zeitraum ---${NC}"
printf "+-------------+--------------------------------+\n"; printf "| Zeitfenster | Empfohlener Rückblick (Tage)   |\n"; printf "+-------------+--------------------------------+\n"; printf "| 5m, 15m     | 15 - 90 Tage                   |\n"; printf "| 30m, 1h     | 180 - 365 Tage                 |\n"; printf "| 2h, 4h      | 550 - 730 Tage                 |\n"; printf "| 6h, 1d      | 1095 - 1825 Tage               |\n"; printf "+-------------+--------------------------------+\n"
read -p "Startdatum (JJJJ-MM-TT) oder 'a' für Automatik [Standard: a]: " START_DATE_INPUT; START_DATE_INPUT=${START_DATE_INPUT:-a}
# *** ENDE DER EINFÜGUNG ***

read -p "Enddatum (JJJJ-MM-TT) [Standard: Heute]: " END_DATE; END_DATE=${END_DATE:-$(date +%F)}
read -p "Startkapital in USDT [Standard: 1000]: " START_CAPITAL; START_CAPITAL=${START_CAPITAL:-1000}
read -p "CPU-Kerne [Standard: -1 für alle]: " N_CORES; N_CORES=${N_CORES:--1}
read -p "Anzahl Trials [Standard: 200]: " N_TRIALS; N_TRIALS=${N_TRIALS:-200}

echo -e "\n${YELLOW}Wähle einen Optimierungs-Modus:${NC}"; echo "  1) Strenger Modus (Profitabel & Sicher)"; echo "  2) 'Finde das Beste'-Modus (Max Profit)"
read -p "Auswahl (1-2) [Standard: 1]: " OPTIM_MODE; OPTIM_MODE=${OPTIM_MODE:-1}
if [ "$OPTIM_MODE" == "1" ]; then
    OPTIM_MODE_ARG="strict"; read -p "Max Drawdown % [Standard: 30]: " MAX_DD; MAX_DD=${MAX_DD:-30}; read -p "Min Win-Rate % [Standard: 55]: " MIN_WR; MIN_WR=${MIN_WR:-55}; read -p "Min PnL % [Standard: 0]: " MIN_PNL; MIN_PNL=${MIN_PNL:-0}
else
    OPTIM_MODE_ARG="best_profit"; read -p "Max Drawdown % [Standard: 30]: " MAX_DD; MAX_DD=${MAX_DD:-30}; MIN_WR=0; MIN_PNL=-99999
fi

for symbol in $SYMBOLS; do
    for timeframe in $TIMEFRAMES; do

        # *** HIER WIRD DAS STARTDATUM BERECHNET (FALLS 'a') ***
        if [ "$START_DATE_INPUT" == "a" ]; then
             lookback_days=365 # Standard-Fallback
             case "$timeframe" in 
                 5m|15m) lookback_days=60 ;; 
                 30m|1h) lookback_days=365 ;; 
                 2h|4h) lookback_days=730 ;; 
                 6h|1d) lookback_days=1095 ;; 
             esac
             # Berechne das Startdatum basierend auf dem Lookback
             FINAL_START_DATE=$(date -d "$lookback_days days ago" +%F)
             echo -e "${YELLOW}INFO: Automatisches Startdatum für $timeframe (${lookback_days} Tage Rückblick) gesetzt auf: $FINAL_START_DATE${NC}"
        else
             FINAL_START_DATE=$START_DATE_INPUT
        fi
        # *** ENDE DATUMSBRECHNUNG ***

        echo -e "\n${BLUE}=======================================================${NC}";
        echo -e "${BLUE}  Bearbeite Pipeline für: $symbol ($timeframe)${NC}";
        # *** Angepasst: Zeige das finale Startdatum an ***
        echo -e "${BLUE}  Datenzeitraum: $FINAL_START_DATE bis $END_DATE${NC}";
        echo -e "${BLUE}=======================================================${NC}"

        echo -e "\n${GREEN}>>> Starte SMC-Optimierung für $symbol ($timeframe)...${NC}"
        python3 "$OPTIMIZER" --symbols "$symbol" --timeframes "$timeframe" \
            --start_date "$FINAL_START_DATE" --end_date "$END_DATE" \
            --jobs "$N_CORES" --max_drawdown "$MAX_DD" \
            --start_capital "$START_CAPITAL" --min_win_rate "$MIN_WR" \
            --trials "$N_TRIALS" --min_pnl "$MIN_PNL" --mode "$OPTIM_MODE_ARG"
        
        if [ $? -ne 0 ]; then
            echo -e "${RED}Fehler im Optimierer für $symbol ($timeframe). Überspringe...${NC}";
        fi
    done
done

deactivate
echo -e "\n${BLUE}✔ Alle Pipeline-Aufgaben erfolgreich abgeschlossen!${NC}"
