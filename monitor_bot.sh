#!/bin/bash

# --- Dynamische Pfadermittlung ---
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)

# Pfade zu wichtigen Dateien und Verzeichnissen
CONFIG_FILE="$SCRIPT_DIR/code/strategies/envelope/config.json"
LOG_FILE="$SCRIPT_DIR/logs/envelope.log"
PYTHON_VENV="$SCRIPT_DIR/code/.venv/bin/python3"
BACKTEST_SCRIPT="$SCRIPT_DIR/code/analysis/backtest.py"
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
    echo -e "${CYAN}                            JÄGER BOT - OPTIMIZER                             ${NC}"
    echo -e "${CYAN}=======================================================${NC}"
    read -p "Bitte geben Sie das Startdatum ein (z.B. 2025-07-01): " START_DATE
    read -p "Bitte geben Sie das Enddatum ein (z.B. 2025-08-18): " END_DATE
    read -p "Handelspaar(e) eingeben (getrennt durch Leerzeichen, z.B. BTC ETH): " SYMBOLS
    read -p "Gewünschten Hebel eingeben (optional, Enter für Standard): " LEVERAGE
    read -p "Startkapital eingeben (optional, z.B. 1000): " START_CAPITAL
    
    echo -e "\n${YELLOW}Wählen Sie die zu testenden Zeitrahmen-Paare (HTF/LTF):${NC}"
    available_pairs=("1d/15m" "4h/5m" "1h/1m")
    selected_pairs=()
    for i in "${!available_pairs[@]}"; do echo "  [$((i+1))] ${available_pairs[$i]}"; done
    read -p "Geben Sie die Nummern ein (z.B. '1 2' für die ersten beiden): " tf_choices
    for choice in $tf_choices; do selected_pairs+=("${available_pairs[$((choice-1))]}") ; done
    timeframes_arg=$(IFS=,; echo "${selected_pairs[*]}")

    echo -e "\n${YELLOW}Wählen Sie den SMA Filter-Modus:${NC}"
    echo "  [1] Nur aktiviert testen"; echo "  [2] Nur deaktiviert testen"; echo "  [3] Beides testen"
    read -p "Geben Sie die Nummer ein [1-3]: " filter_choice
    filter_mode_arg=""
    sma_periods=""
    case $filter_choice in
        1) filter_mode_arg="on" ;; 2) filter_mode_arg="off" ;; 3) filter_mode_arg="both" ;;
        *) echo "${RED}Ungültige Auswahl.${NC}"; exit 1 ;;
    esac

    if [ "$filter_mode_arg" != "off" ]; then
        echo -e "\n${YELLOW}Welche SMA-Perioden sollen getestet werden?${NC}"
        read -p "Geben Sie die Perioden ein (getrennt durch Leerzeichen, z.B. 20 50): " sma_periods
    fi

    echo -e "\n${YELLOW}Welche 'Initial SL Platz.'-Werte (%) sollen getestet werden?${NC}"
    read -p "Geben Sie die Werte ein (z.B. '0.2 0.4'): " initial_sls
    
    echo -e "\n${YELLOW}Welche 'TSL Lookback'-Werte (Kerzen) sollen getestet werden?${NC}"
    read -p "Geben Sie die Werte ein (z.B. '3 5'): " tsl_lookbacks
    
    echo -e "\n${YELLOW}Möchten Sie nach einem Ziel-PnL suchen?${NC}"
    read -p "Geben Sie den Ziel-Gewinn in % ein (optional, z.B. 50): " TARGET_PNL

    if [ -z "$START_DATE" ] || [ -z "$END_DATE" ] || [ -z "$SYMBOLS" ] || [ -z "$timeframes_arg" ] || [ -z "$initial_sls" ] || [ -z "$tsl_lookbacks" ]; then
        echo -e "${RED}Fehler: Grundlegende Felder müssen ausgefüllt werden.${NC}"; exit 1; fi
        
    source "$SCRIPT_DIR/code/.venv/bin/activate"
    TEMP_RESULTS_FILE=$(mktemp)
    
    total_coins=$(echo "$SYMBOLS" | wc -w); current_coin_num=0
    echo -e "\n${YELLOW}Die folgenden Handelspaare werden nacheinander optimiert: $SYMBOLS${NC}"
    
    for coin in $SYMBOLS
    do
        ((current_coin_num++))
        echo -e "\n\n${CYAN}### [Coin $current_coin_num/$total_coins] Starte Optimierung für $coin ###${NC}"
        
        cmd_args=(
            "--start" "$START_DATE" "--end" "$END_DATE" "--symbol" "$coin"
            "--timeframes" "$timeframes_arg" "--filter_mode" "$filter_mode_arg"
            "--initial_sls" "$initial_sls" "--tsl_lookbacks" "$tsl_lookbacks"
        )
        if [ -n "$LEVERAGE" ]; then cmd_args+=("--leverage" "$LEVERAGE"); fi
        if [ -n "$START_CAPITAL" ]; then cmd_args+=("--start_capital" "$START_CAPITAL"); fi
        if [ -n "$sma_periods" ]; then cmd_args+=("--sma_periods" "$sma_periods"); fi
        if [ -n "$TARGET_PNL" ]; then cmd_args+=("--target_pnl" "$TARGET_PNL"); fi
        
        output=$(python3 -u "$OPTIMIZER_SCRIPT" "${cmd_args[@]}")
        echo "$output"
        echo "$output" | grep "BEST_RESULT_FOR_SCRIPT" >> "$TEMP_RESULTS_FILE"
    done
    
    echo -e "\n\n${CYAN}######################################################################${NC}"
    echo -e "${CYAN}###              FINALE GESAMTAUSWERTUNG & RISIKOANALYSE (TOP 10)              ###${NC}"
    echo -e "${CYAN}######################################################################${NC}"
    if [ -s "$TEMP_RESULTS_FILE" ]; then
        sorted_results=$(sort -t';' -k2,2 -nr "$TEMP_RESULTS_FILE" | head -n 10)
        platz=1
        
        while IFS= read -r line; do
            pnl=$(echo "$line" | cut -d';' -f2); win_rate=$(echo "$line" | cut -d';' -f3); trades=$(echo "$line" | cut -d';' -f4)
            symbol_full=$(echo "$line" | cut -d';' -f5); symbol_short=$(echo $symbol_full | cut -d'/' -f1)
            timeframes=$(echo "$line" | cut -d';' -f6); htf=$(echo "$timeframes" | cut -d'/' -f1); ltf=$(echo "$timeframes" | cut -d'/' -f2)
            filter_str=$(echo "$line" | cut -d';' -f7)
            filter_enabled="false"; if [[ $filter_str == ON* ]]; then filter_enabled="true"; fi
            sma_period=20; if [[ $filter_str == *SMA* ]]; then sma_period=$(echo $filter_str | grep -oP '[0-9]+'); fi
            tol=$(echo "$line" | cut -d';' -f8); sl=$(echo "$line" | cut -d';' -f9); tsl=$(echo "$line" | cut -d';' -f10)
            final_leverage=$(echo "$line" | cut -d';' -f11); end_capital=$(echo "$line" | cut -d';' -f12)
            
            echo -e "\n${YELLOW}==================== GESAMT-PLATZ $platz ====================${NC}"
            echo -e "  LEISTUNG:"; printf "    Gewinn (PnL):         %.2f %% (Hebel: %.0fx)\n" $pnl $final_leverage
            if [ "$end_capital" != "N/A" ]; then printf "    Endkapital:           %.2f USDT (Start: %s USDT)\n" $end_capital $START_CAPITAL; fi
            printf "    Trefferquote:         %.2f %%\n" $win_rate; printf "    Anzahl Trades:        %d\n" $trades
            echo -e "  BESTE EINSTELLUNGEN:"; echo -e "    Handelspaar:          $symbol_full"; echo -e "    Zeitrahmen:           $timeframes"; echo -e "    SMA Filter:           $filter_str"
            echo -e "    Retest Toleranz:      $tol%"; echo -e "    Initial SL Platz.:    $sl%"; echo -e "    TSL Lookback:         $tsl Kerzen"
            
            echo -e "  RISIKOANALYSE (1x Hebel):"
            backtest_cmd_args=(
                "--start" "$START_DATE" "--end" "$END_DATE" "--symbol" "$symbol_short" "--leverage" "1"
                "--htf" "$htf" "--ltf" "$ltf" "--retest_tolerance" "$tol" "--initial_sl" "$sl" "--tsl_lookback" "$tsl"
                "--sma_filter_enabled" "$filter_enabled" "--sma_period" "$sma_period"
            )
            if [ -n "$START_CAPITAL" ]; then backtest_cmd_args+=("--start_capital" "$START_CAPITAL"); fi
            risk_analysis_output=$(python3 "$BACKTEST_SCRIPT" "${backtest_cmd_args[@]}" 2>/dev/null)
            
            mae_line=$(echo "$risk_analysis_output" | grep "Maximaler Gegenlauf")
            max_lev_line=$(echo "$risk_analysis_output" | grep "Maximal möglicher Hebel")
            rec_lev_line=$(echo "$risk_analysis_output" | grep "Empfohlener Hebel")
            
            printf "    %-27s %s\n" "Maximaler Gegenlauf:" "$(echo $mae_line | cut -d':' -f2 | xargs)"
            
            if [ -n "$START_CAPITAL" ]; then
                pnl_1x_line=$(echo "$risk_analysis_output" | grep "Gesamt-PnL:")
                pnl_1x=$(echo "$pnl_1x_line" | grep -oE '[-+]?[0-9]*\.?[0-9]+' | head -n 1)
                max_lev_val=$(echo $max_lev_line | grep -oE '[0-9]*\.?[0-9]+' | head -n 1)
                rec_lev_val=$(echo $rec_lev_line | grep -oE '[0-9]*\.?[0-9]+' | head -n 1)

                if [[ -n "$pnl_1x" && -n "$rec_lev_val" && -n "$max_lev_val" ]]; then
                    end_cap_max=$(echo "scale=2; $START_CAPITAL * (1 + ($pnl_1x / 100) * $max_lev_val)" | bc -l)
                    end_cap_rec=$(echo "scale=2; $START_CAPITAL * (1 + ($pnl_1x / 100) * $rec_lev_val)" | bc -l)
                    
                    printf "    %-27s %s -> Endkapital: %s USDT\n" "Maximal möglicher Hebel:" "$(echo $max_lev_line | cut -d':' -f2 | xargs)" "$end_cap_max"
                    printf "    %-27s %s -> Endkapital: %s USDT\n" "Empfohlener Hebel:" "$(echo $rec_lev_line | cut -d':' -f2 | xargs)" "$end_cap_rec"
                else
                    printf "    %-27s %s\n" "Maximal möglicher Hebel:" "$(echo $max_lev_line | cut -d':' -f2 | xargs)"
                    printf "    %-27s %s -> Endkapital: Berechnung fehlgeschlagen\n" "Empfohlener Hebel:" "$(echo $rec_lev_line | cut -d':' -f2 | xargs)"
                fi
            else
                printf "    %-27s %s\n" "Maximal möglicher Hebel:" "$(echo $max_lev_line | cut -d':' -f2 | xargs)"
                printf "    %-27s %s\n" "Empfohlener Hebel:" "$(echo $rec_lev_line | cut -d':' -f2 | xargs)"
            fi
            
            ((platz++))
        done <<< "$sorted_results"
        echo -e "\n${YELLOW}========================================================${NC}"
    else
        echo -e "${RED}Keine gültigen Ergebnisse für die Endauswertung gefunden.${NC}"
    fi
    rm -f "$TEMP_RESULTS_FILE"
    echo -e "\n${GREEN}Alle Optimierungsläufe sind abgeschlossen.${NC}"
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
        else echo -e "${RED}Aktion abgebrochen.${NC}"; fi
        exit 0
        ;;
esac

# ######################################################################
# ### STANDARD-MONITORING-TEIL ###
# ######################################################################
echo -e "${CYAN}=======================================================${NC}"
echo -e "${CYAN}              JÄGER TRADING BOT MONITORING               ${NC}"
echo -e "${CYAN}=======================================================${NC}"
echo "Verwende './monitor_bot.sh <mode>', Modi: ${GREEN}optimize, clear-cache${NC}"
echo -e "Letzte Aktualisierung: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# --- Konfiguration & Strategie ---
echo -e "${YELLOW}--- KONFIGURATION & STRATEGIE (JÄGER) ---${NC}"
if [ -f "$CONFIG_FILE" ]; then
    if command -v jq &> /dev/null; then
        SYMBOL=$(jq -r '.symbol' $CONFIG_FILE); LEVERAGE=$(jq -r '.leverage' $CONFIG_FILE)
        HTF=$(jq -r '.htf_timeframe' $CONFIG_FILE); LTF=$(jq -r '.ltf_timeframe' $CONFIG_FILE)
        echo "Symbol: $SYMBOL, Hebel: ${LEVERAGE}x"; echo "Zeitebenen: HTF $HTF / LTF $LTF"
    else echo -e "${RED}Fehler: 'jq' ist nicht installiert.${NC}"; fi
else echo -e "${RED}Fehler: Konfigurationsdatei nicht gefunden.${NC}"; fi
echo ""

# --- Bot-Statistiken aus dem Log ---
echo -e "${YELLOW}--- BOT-STATISTIKEN (seit Log-Start) ---${NC}"
if [ -f "$LOG_FILE" ]; then
    TRADES_OPENED=$(grep -c -- "-Position @ .* eröffnet" "$LOG_FILE")
    TP1_HIT=$(grep -c "TP1 erreicht" "$LOG_FILE")
    echo "Eröffnete Trades: ${GREEN}$TRADES_OPENED${NC}, Davon TP1 erreicht: ${GREEN}$TP1_HIT${NC}"
else echo "Log-Datei nicht gefunden."; fi
echo ""

# --- Aktuelle Position & Risiko ---
echo -e "${YELLOW}--- AKTUELLE POSITION & RISIKO ---${NC}"
if [ -f "$LOG_FILE" ]; then
    LAST_OPEN_LINE=$(grep -- "-Position @ .* eröffnet" "$LOG_FILE" | tail -n 1)
    LAST_CLOSE_LINE=$(grep "extern geschlossen" "$LOG_FILE" | tail -n 1)

    if [ -n "$LAST_OPEN_LINE" ] && [ "$(echo -e "$LAST_OPEN_LINE\n$LAST_CLOSE_LINE" | sort | tail -n 1)" == "$LAST_OPEN_LINE" ]; then
        POSITION_INFO=$(echo "$LAST_OPEN_LINE" | sed 's/.*UTC: //')
        ENTRY_SIDE_RAW=$(echo "$POSITION_INFO" | awk '{print $1}')
        ENTRY_SIDE="${ENTRY_SIDE_RAW/-Position/}"
        ENTRY_PRICE=$(echo "$POSITION_INFO" | grep -oP '@ \K[0-9.]+')
        STOP_LOSS_PRICE=$(grep -oP '(Initialer SL:|Neuer SL bei) \K[0-9.]+' "$LOG_FILE" | tail -n 1)
        LAST_OPEN_TIMESTAMP=$(echo "$LAST_OPEN_LINE" | cut -d' ' -f 1,2)
        TP1_AFTER_OPEN=$(awk -v d="$LAST_OPEN_TIMESTAMP" '$1" "$2 >= d' "$LOG_FILE" | grep -c "TP1 erreicht")

        if [ "$TP1_AFTER_OPEN" -gt 0 ]; then
            echo -e "Status: ${CYAN}Position Phase 2 (Runner aktiv)${NC}"
        else
            echo -e "Status: ${GREEN}Position Phase 1 (Wartet auf TP1)${NC}"
        fi
        echo -e "Seite: ${GREEN}${ENTRY_SIDE}${NC}, Einstieg: ${GREEN}${ENTRY_PRICE}${NC}, Aktueller SL: ${RED}${STOP_LOSS_PRICE:-N/A}${NC}"
    else
        echo -e "Status: ${CYAN}Keine Position offen${NC}"
    fi
else
    echo "Log-Datei nicht gefunden."
fi
echo ""

# --- SYSTEM-STATUS (KORRIGIERTER TEIL) ---
echo -e "${YELLOW}--- SYSTEM-STATUS ---${NC}"
if [ -f "$LOG_FILE" ]; then
    if [ -s "$LOG_FILE" ]; then
        # Finde die letzte Zeile, die mit einem Zeitstempel beginnt, um Leerzeilen am Ende zu ignorieren
        LAST_LOG_LINE=$(grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}' "$LOG_FILE" | tail -n 1)
        
        if [[ -n "$LAST_LOG_LINE" ]]; then
            LAST_LOG_TIMESTAMP_STR=$(echo "$LAST_LOG_LINE" | cut -d' ' -f 1,2)
            LAST_LOG_SECONDS=$(date -d "$LAST_LOG_TIMESTAMP_STR" +%s)
            MINUTES_AGO=$((( $(date +%s) - LAST_LOG_SECONDS) / 60))
            echo "Letzte Aktivität: ${GREEN}vor $MINUTES_AGO Minuten${NC}"
        else
            echo -e "Letzte Aktivität: ${RED}Keine gültige Log-Zeile gefunden.${NC}"
        fi
    else
        echo "Letzte Aktivität: ${YELLOW}Log-Datei ist leer.${NC}"
    fi
    
    ERROR_COUNT=$(grep -c -iE "Fehler|error" "$LOG_FILE")
    [ "$ERROR_COUNT" -gt 0 ] && echo -e "Fehlerzähler: ${RED}${ERROR_COUNT} Fehler protokolliert${NC}" || echo -e "Fehlerzähler: ${GREEN}Keine Fehler${NC}"
    
    if [ "$ERROR_COUNT" -gt 0 ]; then
        echo ""; echo -e "${YELLOW}--- LETZTE FEHLERMELDUNGEN ---${NC}"
        grep -iE "Fehler|error" "$LOG_FILE" | tail -n 5 | while IFS= read -r line; do echo -e "${RED}- $line${NC}"; done
    fi
else
    echo -e "${RED}Keine Log-Datei gefunden unter $LOG_FILE${NC}"
fi
echo -e "${CYAN}=======================================================${NC}"
