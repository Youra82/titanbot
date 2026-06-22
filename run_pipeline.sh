#!/bin/bash
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

VENV_PATH=".venv/bin/activate"
PYTHON=".venv/bin/python3"
OPTIMIZER="src/titanbot/analysis/optimizer.py"
TODAY=$(date +%F)

source "$VENV_PATH"
echo -e "${GREEN}✔ Virtuelle Umgebung wurde erfolgreich aktiviert.${NC}"

echo ""
echo -e "${BLUE}=======================================================${NC}"
echo "       TitanBot SMC Optimierungs-Pipeline"
echo -e "${BLUE}=======================================================${NC}"

# --- Aufräumen ---
echo ""
echo -e "${YELLOW}Möchtest du alle alten, generierten Configs vor dem Start löschen?${NC}"
read -p "Dies wird für einen kompletten Neustart empfohlen. (j/n) [Standard: n]: " CLEANUP_CHOICE
CLEANUP_CHOICE=${CLEANUP_CHOICE:-n}
if [[ "$CLEANUP_CHOICE" == "j" || "$CLEANUP_CHOICE" == "J" ]]; then
    rm -f src/titanbot/strategy/configs/config_*.json
    rm -f artifacts/results/last_optimizer_run.json
    rm -f artifacts/db/optuna_studies_smc.db
    rm -rf data/cache/
    echo -e "${GREEN}✔ Kompletter Neustart — Configs, Optimizer-Ergebnis, Optuna-DB und Cache gelöscht.${NC}"
else
    echo -e "${GREEN}✔ Alte Configs werden beibehalten.${NC}"
fi

# --- Paare & Zeitfenster ---
echo ""
read -p "Handelspaar(e) eingeben (ohne /USDT, z.B. BTC ETH SOL) [leer=auto]: " SYMBOLS
read -p "Zeitfenster eingeben (z.B. 1h 4h) [leer=auto]: " TIMEFRAMES

if [ -z "$SYMBOLS" ]; then
    SYMBOLS=$("$PYTHON" -c "
import json
s = json.load(open('settings.json'))
pairs = s.get('optimization_settings', {}).get('candidate_strategies', s.get('live_trading_settings', {}).get('active_strategies', []))
seen = {}
for p in pairs:
    sym = p.get('symbol', '').split('/')[0]
    if sym and sym not in seen:
        seen[sym] = True
print(' '.join(seen.keys()))
" 2>/dev/null)
    echo -e "  ${BLUE}Auto-Paare: $SYMBOLS${NC}"
fi
if [ -z "$TIMEFRAMES" ]; then
    TIMEFRAMES=$("$PYTHON" -c "
import json
s = json.load(open('settings.json'))
pairs = s.get('optimization_settings', {}).get('candidate_strategies', s.get('live_trading_settings', {}).get('active_strategies', []))
seen = {}
for p in pairs:
    tf = p.get('timeframe', '')
    if tf and tf not in seen:
        seen[tf] = True
print(' '.join(seen.keys()))
" 2>/dev/null)
    echo -e "  ${BLUE}Auto-Zeitfenster: $TIMEFRAMES${NC}"
fi

# --- OOS-SPLIT ---
echo ""
echo -e "${BLUE}=======================================================${NC}"
echo "  Walk-Forward Out-of-Sample Test (optional)"
echo -e "${BLUE}=======================================================${NC}"
echo ""
echo "  Konzept:"
echo "    Du gibst ein End-Datum ein (z.B. heute)."
echo "    Der 70/30-Split wird automatisch je Timeframe berechnet:"
echo "    30% des Lookbacks = verborgen, 70% = Training."
echo ""
echo "      1h  → 365 Tage: 255 Training + 110 OOS (ab ~2026-03)"
echo "      2h  → 730 Tage: 511 Training + 219 OOS (ab ~2025-12)"
echo "      4h  → 730 Tage: 511 Training + 219 OOS (ab ~2025-12)"
echo "      6h  → 730 Tage: 511 Training + 219 OOS (ab ~2025-12)"
echo ""
echo "  Optionen:  JJJJ-MM-TT (End-Datum, z.B. heute) | leer=kein OOS"
echo ""
read -p "End-Datum für 70/30-Split eingeben [leer=kein OOS]: " OOS_INPUT

OOS_MODE=""
OOS_REF_DATE=""

if [ -n "$OOS_INPUT" ]; then
    OOS_MODE="auto"
    OOS_REF_DATE="$OOS_INPUT"
    echo ""
    echo -e "${GREEN}✔ 70/30-Split aktiv — End-Datum: $OOS_REF_DATE${NC}"
    echo "  (OOS-Startpunkt wird je Timeframe automatisch berechnet)"
    "$PYTHON" -c "
import json
s = json.load(open('settings.json'))
s.setdefault('optimization_settings', {})['oos_reference_date'] = '${OOS_REF_DATE}'
s['optimization_settings']['_oos_note'] = 'End-Datum fuer 70/30-Split. OOS-Start automatisch je Timeframe.'
json.dump(s, open('settings.json', 'w'), indent=4)
" 2>/dev/null || true
else
    echo -e "${GREEN}✔ Kein OOS — kompletter Zeitraum wird genutzt.${NC}"
    "$PYTHON" -c "
import json
s = json.load(open('settings.json'))
s.setdefault('optimization_settings', {})['oos_reference_date'] = None
json.dump(s, open('settings.json', 'w'), indent=4)
" 2>/dev/null || true
fi

# --- Startdatum ---
echo ""
echo -e "${BLUE}--- Empfehlung: Optimaler Rückblick-Zeitraum ---${NC}"
printf "+------------------+----------------------------------------------+\n"
printf "| Zeitfenster      | Lookback  | 70%% Training | 30%% OOS           |\n"
printf "+------------------+----------------------------------------------+\n"
printf "| 5m, 15m          |  60 Tage  |  42 Tage      |  18 Tage           |\n"
printf "| 30m, 1h          | 365 Tage  | 255 Tage      | 110 Tage           |\n"
printf "| 2h, 4h, 6h       | 730 Tage  | 511 Tage      | 219 Tage           |\n"
printf "| 1d               |1095 Tage  | 766 Tage      | 329 Tage           |\n"
printf "+------------------+----------------------------------------------+\n"
echo ""
read -p "Startdatum (JJJJ-MM-TT) oder 'a' für Automatik [Standard: a]: " START_DATE_INPUT
START_DATE_INPUT=${START_DATE_INPUT:-a}

_DEFAULT_CAPITAL=$("$PYTHON" -c "import json; d=json.load(open('settings.json')); print(int(d['optimization_settings']['start_capital']))" 2>/dev/null || echo "20")
read -p "Startkapital in USDT [Standard: ${_DEFAULT_CAPITAL}]: " START_CAPITAL
START_CAPITAL=${START_CAPITAL:-$_DEFAULT_CAPITAL}
read -p "CPU-Kerne [Standard: -1 für alle]: " N_CORES
N_CORES=${N_CORES:--1}
_DEFAULT_TRIALS=$("$PYTHON" -c "import json; d=json.load(open('settings.json')); print(int(d.get('optimization_settings',{}).get('num_trials',200)))" 2>/dev/null || echo "200")
read -p "Anzahl Trials [Standard: ${_DEFAULT_TRIALS}]: " N_TRIALS
N_TRIALS=${N_TRIALS:-$_DEFAULT_TRIALS}

echo ""
echo -e "${YELLOW}Wähle einen Optimierungs-Modus:${NC}"
echo "  1) Strenger Modus    (Profitabel + WR >= Min. Win-Rate + MaxDD <= Limit)"
echo "  2) Best-Profit-Modus (Nur MaxDD-Limit, maximiert PnL)"
read -p "Auswahl (1-2) [Standard: 1]: " OPTIM_MODE_CHOICE
OPTIM_MODE_CHOICE=${OPTIM_MODE_CHOICE:-1}
if [ "$OPTIM_MODE_CHOICE" == "1" ]; then
    OPTIM_MODE_ARG="strict"
    read -p "Max Drawdown % [Standard: 30]: " MAX_DD; MAX_DD=${MAX_DD:-30}
    read -p "Min Win-Rate % [Standard: 55]: " MIN_WR; MIN_WR=${MIN_WR:-55}
    read -p "Min PnL % [Standard: 0]: " MIN_PNL; MIN_PNL=${MIN_PNL:-0}
else
    OPTIM_MODE_ARG="best_profit"
    read -p "Max Drawdown % [Standard: 30]: " MAX_DD; MAX_DD=${MAX_DD:-30}
    MIN_WR=0
    MIN_PNL=-99999
fi

# --- Config-Schutz-Modus ---
OVERWRITE_ALL=""

# --- Schleife pro Symbol + Timeframe ---
for symbol in $SYMBOLS; do
    for timeframe in $TIMEFRAMES; do

        # Config-Pfad ermitteln (Sonderzeichen entfernen wie im Optimizer)
        SYM_CLEAN=$(echo "$symbol" | tr -d '/:-')
        CONFIG_FILE="src/titanbot/strategy/configs/config_${SYM_CLEAN}USDT_${timeframe}.json"
        # Fallback: ohne "USDT"-Suffix (z.B. BTCUSDTUSDT_4h)
        if [ ! -f "$CONFIG_FILE" ]; then
            CONFIG_FILE2="src/titanbot/strategy/configs/config_${SYM_CLEAN}_${timeframe}.json"
        fi

        if ls src/titanbot/strategy/configs/config_*${SYM_CLEAN}*_${timeframe}.json 2>/dev/null | grep -q .; then
            if [ "$OVERWRITE_ALL" != "j" ]; then
                echo ""
                echo -e "${YELLOW}⚠  Config existiert bereits: $symbol ($timeframe)${NC}"
                read -p "   (ü)berschreiben / (s)kipppen / (a)lle überschreiben? [s]: " OVERWRITE_CHOICE
                OVERWRITE_CHOICE=${OVERWRITE_CHOICE:-s}
                case "$OVERWRITE_CHOICE" in
                    ü|u) echo -e "  ${GREEN}→ Wird neu optimiert.${NC}" ;;
                    a)   OVERWRITE_ALL="j"; echo -e "  ${GREEN}→ Alle restlichen werden überschrieben.${NC}" ;;
                    *)   echo -e "  ${YELLOW}→ Übersprungen.${NC}"; continue ;;
                esac
            fi
        fi

        # Lookback je Timeframe
        lookback_days=730
        case "$timeframe" in
            5m|15m) lookback_days=60  ;;
            30m|1h) lookback_days=365 ;;
            2h|4h|6h) lookback_days=730 ;;
            1d)     lookback_days=1095 ;;
        esac

        # OOS-Split pro Timeframe berechnen
        if [ "$OOS_MODE" == "auto" ]; then
            oos_days_tf=$(( lookback_days * 30 / 100 ))
            OOS_START_TF=$(date -d "$OOS_REF_DATE - $oos_days_tf days" +%F)
            CURRENT_END_DATE=$(date -d "$OOS_START_TF - 1 day" +%F)
            if [ "$START_DATE_INPUT" == "a" ]; then
                CURRENT_START_DATE=$(date -d "$OOS_REF_DATE - $lookback_days days" +%F)
            else
                CURRENT_START_DATE="$START_DATE_INPUT"
            fi
        else
            OOS_START_TF=""
            if [ "$START_DATE_INPUT" == "a" ]; then
                CURRENT_START_DATE=$(date -d "$TODAY - $lookback_days days" +%F)
            else
                CURRENT_START_DATE="$START_DATE_INPUT"
            fi
            CURRENT_END_DATE="$TODAY"
        fi

        echo ""
        echo -e "${BLUE}=======================================================${NC}"
        echo -e "${BLUE}  Bearbeite Pipeline für: $symbol ($timeframe)${NC}"
        echo -e "${BLUE}  Trainingszeitraum: $CURRENT_START_DATE  →  $CURRENT_END_DATE${NC}"
        if [ -n "$OOS_START_TF" ]; then
            train_days_show=$(( ($(date -d "$CURRENT_END_DATE" +%s) - $(date -d "$CURRENT_START_DATE" +%s)) / 86400 + 1 ))
            oos_days_show=$(( ($(date -d "$OOS_REF_DATE" +%s) - $(date -d "$OOS_START_TF" +%s)) / 86400 + 1 ))
            echo ""
            echo "  ────────────────────────────────────────────────────────────────"
            printf "  ◄── TRAINING (%d Tage) ──►  ◄── OOS (%d Tage, verborgen) ──►\n" \
                "$train_days_show" "$oos_days_show"
            printf "  %-24s %-14s  %-12s  %s\n" \
                "$CURRENT_START_DATE" "$CURRENT_END_DATE" "$OOS_START_TF" "$OOS_REF_DATE"
            echo "  ────────────────────────────────────────────────────────────────"
        fi
        echo -e "${BLUE}=======================================================${NC}"

        echo -e "\n${GREEN}>>> Starte SMC-Optimierung für $symbol ($timeframe)...${NC}"
        "$PYTHON" "$OPTIMIZER" \
            --symbols       "$symbol" \
            --timeframes    "$timeframe" \
            --start_date    "$CURRENT_START_DATE" \
            --end_date      "$CURRENT_END_DATE" \
            --jobs          "$N_CORES" \
            --max_drawdown  "$MAX_DD" \
            --start_capital "$START_CAPITAL" \
            --min_win_rate  "$MIN_WR" \
            --trials        "$N_TRIALS" \
            --min_pnl       "$MIN_PNL" \
            --mode          "$OPTIM_MODE_ARG"

        if [ $? -ne 0 ]; then
            echo -e "${RED}Fehler im Optimierer für $symbol ($timeframe). Überspringe...${NC}"
        else
            echo -e "${GREEN}✔ Optimierung für $symbol ($timeframe) abgeschlossen.${NC}"
        fi
    done
done

echo ""
echo -e "${BLUE}=======================================================${NC}"
echo -e "${BLUE}✔ Alle Optimierungen abgeschlossen!${NC}"
echo -e "${BLUE}=======================================================${NC}"

# --- Settings aktualisieren ---
echo ""
echo -e "${YELLOW}Möchtest du die optimierten Strategien automatisch in settings.json übernehmen?${NC}"
read -p "Settings aktualisieren? (j/n) [Standard: n]: " UPDATE_SETTINGS_CHOICE
UPDATE_SETTINGS_CHOICE=${UPDATE_SETTINGS_CHOICE:-n}

if [[ "$UPDATE_SETTINGS_CHOICE" == "j" || "$UPDATE_SETTINGS_CHOICE" == "J" ]]; then
    echo -e "\n${GREEN}>>> Aktualisiere settings.json...${NC}"
    "$PYTHON" - <<'PYEOF'
import json, os, glob
ROOT = os.path.abspath('.')
settings = json.load(open(os.path.join(ROOT, 'settings.json')))
configs  = glob.glob(os.path.join(ROOT, 'src', 'titanbot', 'strategy', 'configs', 'config_*.json'))
if not configs:
    print("⚠  Keine Config-Dateien gefunden.")
    exit(0)
new_strats = []
for f in sorted(configs):
    cfg = json.load(open(f))
    sym = cfg.get('market', {}).get('symbol')
    tf  = cfg.get('market', {}).get('timeframe')
    if sym and tf and not any(s['symbol']==sym and s['timeframe']==tf for s in new_strats):
        new_strats.append({'symbol': sym, 'timeframe': tf, 'active': True})
        print(f"  ✔ {sym} ({tf})")
settings['live_trading_settings']['active_strategies'] = new_strats
settings['live_trading_settings']['use_auto_optimizer_results'] = True
json.dump(settings, open(os.path.join(ROOT, 'settings.json'), 'w'), indent=4)
print(f"\n✅ settings.json aktualisiert — {len(new_strats)} Strategie(n) aktiv.")
PYEOF
else
    echo -e "${GREEN}✔ settings.json wurde NICHT verändert.${NC}"
fi

deactivate
echo ""
echo -e "${BLUE}=======================================================${NC}"
echo -e "${BLUE}✔ Pipeline abgeschlossen!${NC}"
echo -e "${BLUE}=======================================================${NC}"
