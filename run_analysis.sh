#!/bin/bash
# run_analysis.sh — titanbot Wissenschaftliche Analysen
#
# Alle 19 Analysen unter einem Befehl. Interaktive Auswahl.
# Ergebnisse werden als Chart via Telegram gesendet.
#
# Ausführung:
#   ./run_analysis.sh
#   ./run_analysis.sh --no-telegram    (kein Telegram, nur lokale Ausgabe)

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python3"
ANALYSIS="$SCRIPT_DIR/src/titanbot/analysis"
NO_TELEGRAM=""

for arg in "$@"; do
    [[ "$arg" == "--no-telegram" ]] && NO_TELEGRAM="--no-telegram"
done

if [ ! -f "$PYTHON" ]; then
    echo -e "${RED}FEHLER: .venv nicht gefunden. Erst install.sh ausführen!${NC}"
    exit 1
fi
source "$SCRIPT_DIR/.venv/bin/activate"
export PYTHONPATH="$SCRIPT_DIR/src:${PYTHONPATH}"

# ─── Menü ─────────────────────────────────────────────────────────────────────

echo ""
echo "======================================================="
echo -e "  ${BOLD}titanbot — Wissenschaftliche Analysen${NC}"
echo "======================================================="
echo ""
echo -e "  ${CYAN}── Priorität 1: Fundament ─────────────────────────${NC}"
echo "   1) Walk-Forward Lookback-Analyse"
echo "   2) Slippage & Fee Impact"
echo "   3) Monte Carlo Simulation"
echo "   4) Bootstrap Signifikanztest"
echo ""
echo -e "  ${CYAN}── Priorität 2: Direkte Gewinnoptimierung ──────────${NC}"
echo "   5) RR-Ratio Optimierung          (Walk-Forward)"
echo "   6) ATR Multiplier Sweep          (Walk-Forward)"
echo "   7) SMC Window Sweep              (Walk-Forward)"
echo "   8) Parameter Sensitivity         (Tornado-Diagramm)"
echo ""
echo -e "  ${CYAN}── Priorität 3: Systemverbesserung ─────────────────${NC}"
echo "   9) Regime Performance Analysis"
echo "  10) Tageszeit-Analyse"
echo "  11) Anti-Korrelations-Portfolio"
echo "  12) Kelly Position Sizing"
echo ""
echo -e "  ${CYAN}── Priorität 4–6: Feintuning & Portfolio ───────────${NC}"
echo "  13) SMC Filter Kombinationen"
echo "  14) Order Block Qualitäts-Analyse"
echo "  15) FVG Hit Rate Analyse"
echo "  16) Volatilitäts-Filter Optimierung"
echo "  17) Multi-Timeframe Confirmation Impact"
echo "  18) Drawdown Duration Analysis"
echo "  19) Entry Timing Analyse"
echo ""
echo "   0) Alle Analysen nacheinander ausführen"
echo ""
read -p "Auswahl (0-19): " MODE
MODE="${MODE//[$'\r\n ']/}"
echo ""

# ─── Hilfsfunktionen ──────────────────────────────────────────────────────────

ask_capital() {
    read -p "Startkapital in USDT [Standard: aus settings.json]: " CAP
    CAP="${CAP//[$'\r\n ']/}"
    if ! [[ "$CAP" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then CAP=""; fi
    echo "$CAP"
}

ask_risk() {
    read -p "Risiko pro Trade in % [Standard: aus Config]: " RISK
    RISK="${RISK//[$'\r\n ']/}"
    if ! [[ "$RISK" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then RISK=""; fi
    echo "$RISK"
}

cap_arg() { [[ -n "$1" ]] && echo "--capital $1" || echo ""; }
risk_arg() { [[ -n "$1" ]] && echo "--risk $1" || echo ""; }

run_mode() {
    local m="$1"
    case "$m" in

    # ── 1: Walk-Forward ───────────────────────────────────────────────────────
    1)
        echo -e "${GREEN}▶ Walk-Forward Lookback-Analyse${NC}"
        echo "  Testet Lookback-Fenster 1–8 Wochen, findet den stabilsten Wert."
        echo "  Mehr Wochen = robusteres Signal, aber weniger aktuelle Daten."
        echo ""
        CAP=$(ask_capital)
        read -p "Min. Trades pro Config im Fenster [Standard: 2]: " MIN_T
        MIN_T="${MIN_T//[$'\r\n ']/}"
        if ! [[ "$MIN_T" =~ ^[0-9]+$ ]]; then MIN_T=2; fi
        $PYTHON "$ANALYSIS/walk_forward_test.py" \
            $(cap_arg "$CAP") --min-trades "$MIN_T" $NO_TELEGRAM
        ;;

    # ── 2: Fee Impact ─────────────────────────────────────────────────────────
    2)
        echo -e "${GREEN}▶ Slippage & Fee Impact${NC}"
        echo "  Zeigt ab welcher Gebühr der Bot unrentabel wird (Break-Even)."
        echo ""
        CAP=$(ask_capital)
        RISK=$(ask_risk)
        $PYTHON "$ANALYSIS/fee_impact.py" \
            $(cap_arg "$CAP") $(risk_arg "$RISK") $NO_TELEGRAM
        ;;

    # ── 3: Monte Carlo ────────────────────────────────────────────────────────
    3)
        echo -e "${GREEN}▶ Monte Carlo Simulation${NC}"
        echo "  10.000 zufällige Trade-Reihenfolgen → Konfidenzintervall & Ruin-Risiko."
        echo ""
        read -p "Anzahl Simulationen [Standard: 10000]: " SIMS
        SIMS="${SIMS//[$'\r\n ']/}"
        if ! [[ "$SIMS" =~ ^[0-9]+$ ]]; then SIMS=10000; fi
        CAP=$(ask_capital)
        RISK=$(ask_risk)
        $PYTHON "$ANALYSIS/monte_carlo.py" \
            --simulations "$SIMS" $(cap_arg "$CAP") $(risk_arg "$RISK") $NO_TELEGRAM
        ;;

    # ── 4: Bootstrap Signifikanztest ──────────────────────────────────────────
    4)
        echo -e "${GREEN}▶ Bootstrap Signifikanztest${NC}"
        echo "  Prüft ob Win-Raten statistisch signifikant über Zufall (50%) liegen."
        echo ""
        read -p "Min. Trades pro Config [Standard: 10]: " MIN_S
        MIN_S="${MIN_S//[$'\r\n ']/}"
        if ! [[ "$MIN_S" =~ ^[0-9]+$ ]]; then MIN_S=10; fi
        read -p "Signifikanzniveau Alpha [Standard: 0.05]: " ALPHA
        ALPHA="${ALPHA//[$'\r\n ']/}"
        if ! [[ "$ALPHA" =~ ^0\.[0-9]+$ ]]; then ALPHA=0.05; fi
        $PYTHON "$ANALYSIS/bootstrap_test.py" \
            --min-samples "$MIN_S" --alpha "$ALPHA" $NO_TELEGRAM
        ;;

    # ── 5: RR-Ratio Optimierung ───────────────────────────────────────────────
    5)
        echo -e "${GREEN}▶ RR-Ratio Optimierung (Walk-Forward)${NC}"
        echo "  Findet out-of-sample das optimale Risk:Reward-Verhältnis."
        echo ""
        CAP=$(ask_capital)
        RISK=$(ask_risk)
        $PYTHON "$ANALYSIS/param_optimizer.py" \
            --param rr $(cap_arg "$CAP") $(risk_arg "$RISK") $NO_TELEGRAM
        ;;

    # ── 6: ATR Multiplier Sweep ───────────────────────────────────────────────
    6)
        echo -e "${GREEN}▶ ATR Multiplier Sweep (Walk-Forward)${NC}"
        echo "  Findet out-of-sample den optimalen ATR-Multiplikator für den Stop-Loss."
        echo ""
        CAP=$(ask_capital)
        RISK=$(ask_risk)
        $PYTHON "$ANALYSIS/param_optimizer.py" \
            --param atr_sl $(cap_arg "$CAP") $(risk_arg "$RISK") $NO_TELEGRAM
        ;;

    # ── 7: SMC Window Sweep ───────────────────────────────────────────────────
    7)
        echo -e "${GREEN}▶ SMC Window Sweep (Walk-Forward)${NC}"
        echo "  Findet out-of-sample die optimale swingsLength (SMC-Pivot-Fenster)."
        echo ""
        CAP=$(ask_capital)
        RISK=$(ask_risk)
        $PYTHON "$ANALYSIS/param_optimizer.py" \
            --param smc_window $(cap_arg "$CAP") $(risk_arg "$RISK") $NO_TELEGRAM
        ;;

    # ── 8: Parameter Sensitivity ──────────────────────────────────────────────
    8)
        echo -e "${GREEN}▶ Parameter Sensitivity Analysis${NC}"
        echo "  Tornado-Diagramm: Welche Parameter machen das System fragil?"
        echo "  Breiter Balken = sensitiv = Overfitting-Risiko."
        echo ""
        CAP=$(ask_capital)
        RISK=$(ask_risk)
        $PYTHON "$ANALYSIS/sensitivity.py" \
            $(cap_arg "$CAP") $(risk_arg "$RISK") $NO_TELEGRAM
        ;;

    # ── 9: Regime Performance ─────────────────────────────────────────────────
    9)
        echo -e "${GREEN}▶ Regime Performance Analysis${NC}"
        echo "  Win-Rate pro Markt-Regime (TREND_UP / TREND_DOWN / RANGE / NEUTRAL)."
        echo "  Sollte man bestimmte Regime ausschalten?"
        echo ""
        read -p "Min. Trades pro Regime [Standard: 5]: " MIN_S
        MIN_S="${MIN_S//[$'\r\n ']/}"
        if ! [[ "$MIN_S" =~ ^[0-9]+$ ]]; then MIN_S=5; fi
        $PYTHON "$ANALYSIS/regime_analysis.py" \
            --min-samples "$MIN_S" $NO_TELEGRAM
        ;;

    # ── 10: Tageszeit-Analyse ─────────────────────────────────────────────────
    10)
        echo -e "${GREEN}▶ Tageszeit-Analyse${NC}"
        echo "  Performen SMC-Signale zu bestimmten Uhrzeiten besser?"
        echo "  Asiatische / Europäische / US-Session verglichen."
        echo ""
        $PYTHON "$ANALYSIS/time_analysis.py" $NO_TELEGRAM
        ;;

    # ── 11: Anti-Korrelation ──────────────────────────────────────────────────
    11)
        echo -e "${GREEN}▶ Anti-Korrelations-Portfolio${NC}"
        echo "  Welche Pairs verlieren/gewinnen selten gleichzeitig?"
        echo "  Korrelationsmatrix → Portfolio mit minimalem Drawdown."
        echo ""
        RISK=$(ask_risk)
        $PYTHON "$ANALYSIS/correlation.py" \
            $(risk_arg "$RISK") $NO_TELEGRAM
        ;;

    # ── 12: Kelly Position Sizing ─────────────────────────────────────────────
    12)
        echo -e "${GREEN}▶ Kelly Position Sizing${NC}"
        echo "  Mathematisch optimales Risiko pro Trade (Half-Kelly)."
        echo "  Configs mit hoher Win-Rate dürfen mehr riskieren."
        echo ""
        CAP=$(ask_capital)
        RISK=$(ask_risk)
        $PYTHON "$ANALYSIS/kelly_sizing.py" \
            $(cap_arg "$CAP") $(risk_arg "$RISK") --half-kelly $NO_TELEGRAM
        ;;

    # ── 13: SMC Filter Kombinationen ──────────────────────────────────────────
    13)
        echo -e "${GREEN}▶ SMC Filter Kombinationen${NC}"
        echo "  Testet alle 8 Kombinationen der SMC-Kern-Filter:"
        echo "  P/D-Zone × Liquidity-Sweep × Rejection-Candle."
        echo ""
        CAP=$(ask_capital)
        $PYTHON "$ANALYSIS/filter_impact.py" \
            $(cap_arg "$CAP") $NO_TELEGRAM
        ;;

    # ── 14: Order Block Qualitäts-Analyse ────────────────────────────────────
    14)
        echo -e "${GREEN}▶ Order Block Qualitäts-Analyse${NC}"
        echo "  Optimiert min_ob_quality Schwellwert (0.0–0.5)."
        echo "  Höher = weniger aber qualitativ bessere OB-Entries."
        echo ""
        CAP=$(ask_capital)
        $PYTHON "$ANALYSIS/ob_quality.py" \
            $(cap_arg "$CAP") $NO_TELEGRAM
        ;;

    # ── 15: FVG Hit Rate Analyse ──────────────────────────────────────────────
    15)
        echo -e "${GREEN}▶ FVG Hit Rate Analyse${NC}"
        echo "  Optimiert min_fvg_size_pct Schwellwert (0.02–0.5%)."
        echo "  Kleinere FVGs = mehr Trades aber mehr Noise."
        echo ""
        CAP=$(ask_capital)
        $PYTHON "$ANALYSIS/fvg_analysis.py" \
            $(cap_arg "$CAP") $NO_TELEGRAM
        ;;

    # ── 16: Volatilitäts-Filter ───────────────────────────────────────────────
    16)
        echo -e "${GREEN}▶ Volatilitäts-Filter Optimierung${NC}"
        echo "  ADX-Filter: verschiedene Schwellwerte (15–35) verglichen."
        echo "  Filter-AUS vs Filter-AN — wann hilft ADX wirklich?"
        echo ""
        CAP=$(ask_capital)
        RISK=$(ask_risk)
        $PYTHON "$ANALYSIS/volatility_filter.py" \
            $(cap_arg "$CAP") $(risk_arg "$RISK") $NO_TELEGRAM
        ;;

    # ── 17: Multi-Timeframe Confirmation ─────────────────────────────────────
    17)
        echo -e "${GREEN}▶ Multi-Timeframe Confirmation Impact${NC}"
        echo "  use_mtf_filter=True vs False: Qualität vs. Trade-Anzahl."
        echo "  HTF-Bias filtert gegen-trendige Entries."
        echo ""
        CAP=$(ask_capital)
        $PYTHON "$ANALYSIS/multitf_analysis.py" \
            $(cap_arg "$CAP") $NO_TELEGRAM
        ;;

    # ── 18: Drawdown Duration ─────────────────────────────────────────────────
    18)
        echo -e "${GREEN}▶ Drawdown Duration Analysis${NC}"
        echo "  Wie lange dauern Drawdown-Phasen? Wie lange muss man aussitzen?"
        echo ""
        CAP=$(ask_capital)
        RISK=$(ask_risk)
        $PYTHON "$ANALYSIS/drawdown_duration.py" \
            $(cap_arg "$CAP") $(risk_arg "$RISK") $NO_TELEGRAM
        ;;

    # ── 19: Entry Timing ──────────────────────────────────────────────────────
    19)
        echo -e "${GREEN}▶ Entry Timing Analyse${NC}"
        echo "  Stundenweise Auswertung (0–23h UTC) + Wochentag-Heatmap."
        echo "  Wann signalisiert der SMC-Bot am zuverlässigsten?"
        echo ""
        $PYTHON "$ANALYSIS/entry_timing.py" $NO_TELEGRAM
        ;;

    *)
        echo -e "${RED}Ungültige Auswahl: $m${NC}"
        ;;
    esac
}

# ─── Auswahl ausführen ────────────────────────────────────────────────────────

if [ "$MODE" == "0" ]; then
    echo -e "${YELLOW}▶ Alle 19 Analysen werden nacheinander ausgeführt.${NC}"
    echo "  Standard-Parameter: Kapital/Risiko aus settings.json / Configs."
    echo ""
    for i in $(seq 1 19); do
        echo ""
        echo -e "${CYAN}══════════════════════════════════════════════════════${NC}"
        echo -e "${CYAN}  Analyse $i / 19${NC}"
        echo -e "${CYAN}══════════════════════════════════════════════════════${NC}"
        case "$i" in
            1)  $PYTHON "$ANALYSIS/walk_forward_test.py" \
                    --min-trades 2 $NO_TELEGRAM 2>/dev/null || true ;;
            2)  $PYTHON "$ANALYSIS/fee_impact.py" \
                    $NO_TELEGRAM 2>/dev/null || true ;;
            3)  $PYTHON "$ANALYSIS/monte_carlo.py" \
                    --simulations 10000 $NO_TELEGRAM 2>/dev/null || true ;;
            4)  $PYTHON "$ANALYSIS/bootstrap_test.py" \
                    --min-samples 10 --alpha 0.05 $NO_TELEGRAM 2>/dev/null || true ;;
            5)  $PYTHON "$ANALYSIS/param_optimizer.py" \
                    --param rr $NO_TELEGRAM 2>/dev/null || true ;;
            6)  $PYTHON "$ANALYSIS/param_optimizer.py" \
                    --param atr_sl $NO_TELEGRAM 2>/dev/null || true ;;
            7)  $PYTHON "$ANALYSIS/param_optimizer.py" \
                    --param smc_window $NO_TELEGRAM 2>/dev/null || true ;;
            8)  $PYTHON "$ANALYSIS/sensitivity.py" \
                    $NO_TELEGRAM 2>/dev/null || true ;;
            9)  $PYTHON "$ANALYSIS/regime_analysis.py" \
                    --min-samples 5 $NO_TELEGRAM 2>/dev/null || true ;;
            10) $PYTHON "$ANALYSIS/time_analysis.py" \
                    $NO_TELEGRAM 2>/dev/null || true ;;
            11) $PYTHON "$ANALYSIS/correlation.py" \
                    $NO_TELEGRAM 2>/dev/null || true ;;
            12) $PYTHON "$ANALYSIS/kelly_sizing.py" \
                    --half-kelly $NO_TELEGRAM 2>/dev/null || true ;;
            13) $PYTHON "$ANALYSIS/filter_impact.py" \
                    $NO_TELEGRAM 2>/dev/null || true ;;
            14) $PYTHON "$ANALYSIS/ob_quality.py" \
                    $NO_TELEGRAM 2>/dev/null || true ;;
            15) $PYTHON "$ANALYSIS/fvg_analysis.py" \
                    $NO_TELEGRAM 2>/dev/null || true ;;
            16) $PYTHON "$ANALYSIS/volatility_filter.py" \
                    $NO_TELEGRAM 2>/dev/null || true ;;
            17) $PYTHON "$ANALYSIS/multitf_analysis.py" \
                    $NO_TELEGRAM 2>/dev/null || true ;;
            18) $PYTHON "$ANALYSIS/drawdown_duration.py" \
                    $NO_TELEGRAM 2>/dev/null || true ;;
            19) $PYTHON "$ANALYSIS/entry_timing.py" \
                    $NO_TELEGRAM 2>/dev/null || true ;;
        esac
    done
    echo ""
    echo -e "${GREEN}════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  Alle 19 Analysen abgeschlossen.${NC}"
    echo -e "${GREEN}════════════════════════════════════════════════${NC}"
else
    run_mode "$MODE"
fi

deactivate
