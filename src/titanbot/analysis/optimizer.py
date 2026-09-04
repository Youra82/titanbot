# /root/titanbot/src/titanbot/analysis/optimizer.py (Leverage BEGRENZT auf 5-15, mit MTF-HTF-Speicherung)
import os
import sys
import json
import optuna
import numpy as np
import argparse
import logging
import warnings
from datetime import datetime, timezone, timedelta

# Ohne dies stuerzt jedes print() mit z.B. '→' auf Windows-Konsolen ab (cp1252
# kennt viele Unicode-Zeichen nicht) -- betrifft nicht die VPS-Produktion.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8')
    except Exception:
        pass

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
logging.getLogger('tensorflow').setLevel(logging.ERROR)
logging.getLogger('absl').setLevel(logging.ERROR)
warnings.filterwarnings('ignore', category=UserWarning, module='keras')

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from titanbot.analysis.backtester import load_data, run_smc_backtest, FINE_TF_MAP, LazyFineData
from titanbot.analysis.evaluator import evaluate_dataset

optuna.logging.set_verbosity(optuna.logging.WARNING)

# Empfohlener Lookback je Timeframe (wenn --start_date auto übergeben wird)
TF_LOOKBACK_DAYS = {'5m': 60, '15m': 60, '30m': 365, '1h': 365,
                    '2h': 730, '4h': 730, '6h': 730, '1d': 1095}

import math
import threading as _threading

HISTORICAL_DATA = None
FINE_DATA = None  # feinere Kerzen fuer SL/TP-Intrabar-Reihenfolgen-Aufloesung (oraclebot-Muster)
TRAIN_DATA      = None   # 70% — Optimierung
TEST_DATA       = None   # 30% — Out-of-Sample Validierung
TRAIN_SPLIT_IDX = 0
CURRENT_SYMBOL = None
CURRENT_TIMEFRAME = None

# Separate SMC-Caches für Train- und Test-Datensatz
_SMC_TRAIN_CACHE: dict = {}
_SMC_TRAIN_CACHE_LOCK = _threading.Lock()
_SMC_TEST_CACHE: dict = {}
_SMC_TEST_CACHE_LOCK  = _threading.Lock()

CONFIG_SUFFIX = ""
MAX_DRAWDOWN_CONSTRAINT = 0.30
MIN_WIN_RATE_CONSTRAINT = 55.0
MIN_PNL_CONSTRAINT = 0.0
START_CAPITAL = 1000
OPTIM_MODE = "strict"
MIN_TRADES_PER_YEAR = 300   # Default; wird immer per --min_trades_per_year CLI-Arg ueberschrieben
K_FOLDS = 3                 # Default; wird immer per --k_folds CLI-Arg ueberschrieben

def create_safe_filename(symbol, timeframe):
    return f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"


def _seed_from_previous_study(study, symbol, timeframe, storage_url):
    """
    Warm-Start: speist den besten (per rohem test_pnl) Trial einer frueheren,
    kleineren Suche fuer dasselbe Paar als garantierten ersten Versuch in die
    NEUE (ggf. groessere) Suche ein. Sonst kann eine Suchraum-Erweiterung
    (z.B. neue Filter-Optionen) den alten Fund verpassen, weil das Trial-Budget
    ueber einen groesseren Raum verteilt wird und die neue Studie bei null
    anfaengt statt auf den alten Trials aufzubauen -- beobachtet 2026-08-28
    bei ADA/30m (alter Fund +1.11%, neue Suche ohne Warm-Start: -25.35%).
    """
    for prev_suffix in ('_robust', '_v2'):
        prev_name = f"smc_{create_safe_filename(symbol, timeframe)}{prev_suffix}_{OPTIM_MODE}"
        try:
            prev_study = optuna.load_study(study_name=prev_name, storage=storage_url)
        except Exception:
            continue
        prev_trials = [t for t in prev_study.trials if t.state == optuna.trial.TrialState.COMPLETE]
        if not prev_trials:
            continue
        best_prev = max(prev_trials, key=lambda t: t.user_attrs.get('test_pnl', -1e9))
        seed_params = dict(best_prev.params)
        # Parameter, die die alte (kleinere) Suche noch nicht kannte, mit den
        # damals fest verdrahteten Werten auffuellen -- reproduziert den alten
        # Fund 1:1 als Ausgangspunkt der neuen, groesseren Suche.
        seed_params.setdefault('use_pd_filter', True)
        seed_params.setdefault('use_liquidity_sweep_filter', True)
        seed_params.setdefault('use_rejection_candle', True)
        seed_params.setdefault('use_momentum_filter', False)
        seed_params.setdefault('momentum_require_both', True)
        seed_params.setdefault('use_trailing_stop', False)
        try:
            study.enqueue_trial(seed_params, skip_if_exists=True)
            print(f"  Warm-Start: bester Fund aus '{prev_name}' "
                  f"(Test-PnL {best_prev.user_attrs.get('test_pnl')}%) eingespeist.")
        except Exception as e:
            print(f"  WARN: Warm-Start aus '{prev_name}' fehlgeschlagen: {e}")
        return

def _get_smc_precomputed(cache, cache_lock, data, smc_params):
    """
    SMC-Engine-Ergebnis aus Cache holen oder berechnen.

    WICHTIG: der Cache-Key MUSS das Datenset identifizieren (id(data), len(data)),
    nicht nur die SMC-Parameter. TRAIN_DATA/TEST_DATA sind modul-globale Objekte,
    die der main()-Loop pro Symbol/Timeframe neu zuweist; bei n_jobs>1 laufen
    Trials als Threads parallel. Ohne Datenset-Fingerprint im Key kann ein noch
    laufender Thread aus Paar N (z.B. wenn study.optimize() zurueckkehrt, bevor
    wirklich alle Worker-Threads fertig sind) einen Cache-Eintrag fuer Paar N+1
    unter demselben (swingsLength, ob_mitigation, liquidity_lookback)-Key
    ueberschreiben -- fuehrte am 2026-08-28 bei mehreren Paaren (u.a. XRP/6h) zu
    "Length of values (X) does not match length of index (Y)"-Abstuerzen, weil
    SMC-Ergebnisse eines KOMPLETT ANDEREN Paares mit anderer Kerzenzahl
    zurueckgegeben wurden.
    """
    _cache_key = (id(data), len(data),
                  smc_params['swingsLength'], smc_params['ob_mitigation'], smc_params['liquidity_lookback'])
    with cache_lock:
        _precomputed = cache.get(_cache_key)
    if _precomputed is None:
        from titanbot.strategy.smc_engine import SMCEngine as _SMCEng
        _eng = _SMCEng(settings=smc_params)
        _smc_res = _eng.process_dataframe(data[['open', 'high', 'low', 'close']].copy())
        _precomputed = {
            'smc_results': _smc_res,
            'smc_structures': {
                'order_blocks': _eng.swingOrderBlocks + _eng.internalOrderBlocks,
                'fair_value_gaps': _eng.fairValueGaps,
                'events': _eng.event_log,
                'data_times': _eng.times,
            },
        }
        # smc_results already contains all_swing_obs/all_internal_obs/all_fvgs
        # (added by SMCEngine.process_dataframe) — no extra storage needed
        with cache_lock:
            cache.setdefault(_cache_key, _precomputed)
    return _precomputed


def objective(trial):
    smc_params = {
        'swingsLength': trial.suggest_int('swingsLength', 15, 60),
        'ob_mitigation': trial.suggest_categorical('ob_mitigation', ['High/Low', 'Close']),
        'use_adx_filter': trial.suggest_categorical('use_adx_filter', [True, False]),
        'adx_period': 14,
        'adx_threshold': trial.suggest_int('adx_threshold', 20, 30),
        # Waren frueher hart auf True kodiert ("SMC-Kern, daran ruettelt man nicht") --
        # ein manueller Filter-Sweep (2026-08-28) zeigte aber deutliche, paar-/fenster-
        # abhaengige Unterschiede. Jetzt durchsuchbar, damit jedes Paar/Timeframe seine
        # eigene beste Kombination automatisch findet statt einer globalen Annahme.
        'use_pd_filter': trial.suggest_categorical('use_pd_filter', [True, False]),
        'use_liquidity_sweep_filter': trial.suggest_categorical('use_liquidity_sweep_filter', [True, False]),
        'liquidity_lookback': trial.suggest_categorical('liquidity_lookback', [10, 15, 20, 25]),
        'min_fvg_size_pct': trial.suggest_float('min_fvg_size_pct', 0.05, 0.20),
        'min_ob_quality': trial.suggest_float('min_ob_quality', 0.10, 0.50),
        'max_ob_touches': trial.suggest_int('max_ob_touches', 0, 2),
        'use_rejection_candle': trial.suggest_categorical('use_rejection_candle', [True, False]),
        'use_mtf_filter': trial.suggest_categorical('use_mtf_filter', [True, False]),
        # Zwei weitere trade_logic.py-Filter, die bislang NIE durchsucht wurden (fix auf
        # True): use_entry_confirmation verlangt SELBST bei use_rejection_candle=False
        # noch eine in Trade-Richtung gefaerbte Kerze; use_swing_ob steuert ob grosse
        # Swing-Level-OBs (zusaetzlich zu den kleineren Internal-OBs) als Einstiegszonen
        # zaehlen. Beide koennen die Trade-Frequenz pro Paar unnoetig einschraenken.
        'use_entry_confirmation': trial.suggest_categorical('use_entry_confirmation', [True, False]),
        'use_swing_ob': trial.suggest_categorical('use_swing_ob', [True, False]),
        # Zonenbasiertes TP (naechstes ungesweeptes Liquiditaetslevel statt festem
        # R:R-Vielfachen) -- existierte im Code nur fuer ein Nebenwerkzeug, nie im
        # eigentlichen Optimierungs-/Live-Pfad. Authentischerer SMC-Exit-Ansatz
        # ("Preis zielt auf die naechste Liquiditaet"), noch nie getestet.
        'use_zone_based_tp': trial.suggest_categorical('use_zone_based_tp', [True, False]),
        'symbol': CURRENT_SYMBOL,
        'timeframe': CURRENT_TIMEFRAME,
        '_timeframe': CURRENT_TIMEFRAME,
    }
    # Momentum-Filter (MACD-Cross + RSI-Reversal, siehe momentum_indicators.py) als
    # durchsuchbare Option statt fixem An/Aus -- ein einzelner manueller Test mit
    # lookback=3 war zu restriktiv (1-2 Trades/2 Monate), hier lässt Optuna den
    # Lookback selbst finden statt raten.
    # momentum_require_both: A/B-Test (2026-09-04) zeigte, dass die AND-Kombi
    # (MACD-Cross UND RSI-Reversal im selben Fenster) das Signal auf 1-60
    # Trades/9 Monate ausduennt -- die "Verbesserung" dort war ein Sample-Size-
    # Artefakt, kein echter Edge. Die OR-Variante (lookback=3) hatte dagegen
    # echte Handelsmenge (65-205 Trades/Paar) UND eine reale portfolioweite
    # Verbesserung (-2.08%→-0.99% Mittel, 5/7 Paare besser). Jetzt durchsuchbar
    # statt hart auf AND fixiert.
    use_momentum = trial.suggest_categorical('use_momentum_filter', [True, False])
    smc_params['use_momentum_filter'] = use_momentum
    if use_momentum:
        smc_params['momentum_lookback'] = trial.suggest_int('momentum_lookback', 2, 20)
        smc_params['momentum_require_both'] = trial.suggest_categorical('momentum_require_both', [True, False])
    risk_params = {
        'risk_reward_ratio': trial.suggest_float('risk_reward_ratio', 1.5, 4.0),
        'risk_per_trade_pct': 1.0,  # Fest für fairen Vergleich — wird in Mode 3 optimiert
        'min_leverage': trial.suggest_int('min_leverage', 2, 8),
        'max_leverage': trial.suggest_int('max_leverage', 8, 30),
        'atr_multiplier_sl': trial.suggest_float('atr_multiplier_sl', 0.5, 3.0),
    }
    # Trailing-Stop (dnabot-Prinzip: Gewinner ueber das feste TP hinaus laufen
    # lassen statt am R:R-Ziel zu deckeln) -- 'trailing_stop_activation_rr' und
    # 'trailing_stop_callback_rate_pct' wurden bisher gesucht, aber NIRGENDS
    # verwendet (weder Backtest noch Live) -- reines Rauschen im Suchraum.
    # Jetzt echt im Backtester implementiert (siehe backtester.py) und hier
    # als An/Aus-Flag durchsuchbar, analog zum Momentum-Filter-Muster.
    use_trailing = trial.suggest_categorical('use_trailing_stop', [True, False])
    risk_params['use_trailing_stop'] = use_trailing
    if use_trailing:
        risk_params['trailing_stop_activation_rr'] = trial.suggest_float('trailing_stop_activation_rr', 0.5, 3.5)
        risk_params['trailing_stop_callback_rate_pct'] = trial.suggest_float('trailing_stop_callback_rate_pct', 0.5, 2.5)

    # Proportionale Mindest-Trades: MIN_TRADES_PER_YEAR skaliert auf die tatsächliche Datenlänge
    train_days = max(1, (TRAIN_DATA.index[-1] - TRAIN_DATA.index[0]).days)
    test_days  = max(1, (TEST_DATA.index[-1]  - TEST_DATA.index[0]).days)
    min_train_trades = max(2, int(MIN_TRADES_PER_YEAR * train_days / 365))
    min_test_trades  = max(1, int(MIN_TRADES_PER_YEAR * test_days  / 365))

    # ── STUFE 1: TRAIN-Backtest (70% der Daten) — bestimmt AUSSCHLIESSLICH den Score ──
    smc_params['_precomputed_smc'] = _get_smc_precomputed(
        _SMC_TRAIN_CACHE, _SMC_TRAIN_CACHE_LOCK, TRAIN_DATA, smc_params)

    train_result = run_smc_backtest(TRAIN_DATA.copy(), smc_params, risk_params, START_CAPITAL, verbose=False, fine_data=FINE_DATA)
    train_pnl    = train_result.get('total_pnl_pct', -1000)
    train_dd     = train_result.get('max_drawdown_pct', 1.0)
    train_trades = train_result.get('trades_count', 0)
    train_wr     = train_result.get('win_rate', 0)

    if train_trades < min_train_trades or train_dd > MAX_DRAWDOWN_CONSTRAINT:
        raise optuna.exceptions.TrialPruned()

    # ── K-Fold-Robustheit (wie ltbbot): TRAIN_DATA in K_FOLDS gleich grosse,
    # chronologische Teilfenster splitten, jedes EINZELN backtesten -- der
    # Score nutzt danach das SCHLECHTESTE Fenster statt der Gesamt-Train-PnL.
    # Eine gute Gesamt-PnL kann sonst allein aus einem einzelnen guten
    # Abschnitt stammen; bei jungen/duenn-historischen Coins zerfaellt die
    # ohnehin kurze Historie durch K_FOLDS in noch kleinere, statistisch
    # instabile Fenster und wird so besonders hart bestraft -- genau der Fund
    # aus screen_candidates.py (2026-09-04): USELESS/SNDK/KORU wurden mit
    # < 30 Trades ueber einen einzelnen 70/30-Split "bestaetigt". Eigene
    # SMC-Berechnung pro Fold noetig (NICHT den TRAIN-weiten
    # _precomputed_smc-Cache wiederverwenden -- die Bar-Indizes passen sonst
    # nicht zur kuerzeren Fold-Slice).
    if K_FOLDS > 1:
        fold_smc_params = {k: v for k, v in smc_params.items() if k != '_precomputed_smc'}
        fold_size = len(TRAIN_DATA) // K_FOLDS
        fold_pnls = []
        for k in range(K_FOLDS):
            f_start = k * fold_size
            f_end = (k + 1) * fold_size if k < K_FOLDS - 1 else len(TRAIN_DATA)
            fold_data = TRAIN_DATA.iloc[f_start:f_end]
            fold_result = run_smc_backtest(
                fold_data.copy(), fold_smc_params, risk_params, START_CAPITAL,
                verbose=False, fine_data=FINE_DATA)
            fold_pnls.append(fold_result.get('total_pnl_pct', -1000))
        trial.set_user_attr('fold_pnls', [round(p, 2) for p in fold_pnls])
        robust_train_pnl = min(fold_pnls)
    else:
        robust_train_pnl = train_pnl

    # ── STUFE 2: TEST-Backtest (30% der Daten) — NUR fuer Reporting/User-Attrs ──
    # FIXIERT (2026-08-28, dritte Runde): Das Test-Set floss bis eben mit 70% Gewicht
    # direkt in final_score ein -- also in genau das, was Optuna ueber 200-350 Trials
    # hinweg aktiv MAXIMIERT. Damit war das "unsichtbare" Test-Fenster nie wirklich
    # blind: die Bayes'sche Suche lernte over Trials hinweg, welche Parameter dort gut
    # abschneiden, und steuerte gezielt dorthin -- Overfitting auf das OOS-Fenster durch
    # wiederholtes Feedback, nur indirekt statt per Kurvenanpassung. Beweis aus echten
    # VPS-Configs: ADA/30m train_pnl=-14.1% / test_pnl=+30.3%, ETH/1h train_pnl=-16.4% /
    # test_pnl=+25.5% -- die Suche opferte Trainings-Performance zugunsten von Mustern,
    # die nur im Testfenster funktionierten. Ein rollierender Walk-Forward (143-157
    # Fenster, von der Suche nie gesehen) zeigte danach durchgehend negatives PnL.
    # Fix: Test-Metriken werden weiterhin berechnet und als user_attr gespeichert, aber
    # beeinflussen weder Pruning noch final_score. Sie werden AUSSCHLIESSLICH einmalig
    # NACH Abschluss der Suche verwendet, um unter den (rein per Training gerankten)
    # Trials das erste zu waehlen, das sich auch auf dem nie gesehenen Test-Fenster
    # bestaetigt (siehe main(), Trial-Auswahl) -- echtes Trainieren-dann-Validieren
    # statt Trainieren-waehrend-des-Validierens.
    smc_params['_precomputed_smc'] = _get_smc_precomputed(
        _SMC_TEST_CACHE, _SMC_TEST_CACHE_LOCK, TEST_DATA, smc_params)

    test_result  = run_smc_backtest(
        TEST_DATA.copy(), smc_params, risk_params, START_CAPITAL,
        verbose=False, bar_index_offset=TRAIN_SPLIT_IDX, fine_data=FINE_DATA)
    test_pnl     = test_result.get('total_pnl_pct', -1000)
    test_dd      = test_result.get('max_drawdown_pct', 1.0)
    test_trades  = test_result.get('trades_count', 0)
    test_wr      = test_result.get('win_rate', 0)

    # ── Score — AUSSCHLIESSLICH aus Train-Metriken, kein Test-Leakage in die Suche ──
    def signed_log(x):
        return math.copysign(math.log1p(abs(x)), x)

    train_score = signed_log(robust_train_pnl) / max(train_dd * 100, 1.0)
    # Trade-Dichte: kleiner Tie-Breaker zwischen ähnlich profitablen Setups (Train-seitig).
    trade_ratio = train_trades / max(min_train_trades, 1)
    trade_bonus = math.log1p(train_trades) * 0.03 + math.log1p(max(0, trade_ratio - 1.0)) * 0.015
    wr_bonus    = max(0.0, (train_wr - 40.0) / 200.0)    # winziger Bonus ab 40% Win-Rate

    final_score = train_score + trade_bonus + wr_bonus

    # User-Attribute für Config-Export, finale Trial-Auswahl (main()) und Fortschrittsanzeige.
    # min_test_trades/min_train_trades mitspeichern, damit main() nach der Suche pruefen
    # kann ob ein Trial genug Test-Trades fuer eine belastbare Aussage hat.
    trial.set_user_attr('test_pnl',    round(test_pnl,    2))
    trial.set_user_attr('train_pnl',   round(train_pnl,   2))
    trial.set_user_attr('robust_train_pnl', round(robust_train_pnl, 2))
    trial.set_user_attr('test_wr',     round(test_wr,     2))
    trial.set_user_attr('test_trades', test_trades)
    trial.set_user_attr('test_dd_pct', round(test_dd * 100, 2))
    trial.set_user_attr('train_trades', train_trades)
    trial.set_user_attr('min_test_trades', min_test_trades)

    return final_score

def main():
    global HISTORICAL_DATA, FINE_DATA, TRAIN_DATA, TEST_DATA, TRAIN_SPLIT_IDX, CURRENT_SYMBOL, CURRENT_TIMEFRAME, CONFIG_SUFFIX, MAX_DRAWDOWN_CONSTRAINT, MIN_WIN_RATE_CONSTRAINT, MIN_PNL_CONSTRAINT, START_CAPITAL, OPTIM_MODE, MIN_TRADES_PER_YEAR, K_FOLDS
    parser = argparse.ArgumentParser(description="Parameter-Optimierung für TitanBot (SMC)")
    parser.add_argument('--symbols', required=False, type=str, default="")
    parser.add_argument('--timeframes', required=False, type=str, default="")
    parser.add_argument('--pairs', required=False, type=str, default="",
                        help='Paare im Format "SYM1:TF1 SYM2:TF2" (Alternativ zu --symbols + --timeframes)')
    parser.add_argument('--start_date', required=True, type=str)
    parser.add_argument('--end_date', required=True, type=str)
    parser.add_argument('--jobs', required=True, type=int)
    parser.add_argument('--max_drawdown', required=True, type=float)
    parser.add_argument('--start_capital', required=True, type=float)
    parser.add_argument('--min_win_rate', required=True, type=float)
    parser.add_argument('--trials', required=True, type=int)
    parser.add_argument('--min_pnl', required=True, type=float)
    parser.add_argument('--mode', required=True, type=str)
    parser.add_argument('--config_suffix', type=str, default="")
    parser.add_argument('--min_trades_per_year', type=int, default=300,
                        help='Mindest-Trades pro Jahr pro Strategie (proportional auf Datenlänge skaliert)')
    parser.add_argument('--results_file', type=str, default=None,
                        help='Pfad fuer die Run-Summary (Default: artifacts/results/last_optimizer_run.json). '
                             'Fuer Screening-/Testlaeufe auf einen eigenen Pfad umleiten, damit die echte '
                             'Produktions-Summary nicht ueberschrieben wird.')
    parser.add_argument('--k_folds', type=int, default=3,
                        help='TRAIN-Daten in K gleich grosse, chronologische Teilfenster splitten; der Score '
                             'nutzt das SCHLECHTESTE Teilfenster statt der Gesamt-Train-PnL (wie ltbbot). '
                             'Verhindert dass eine gute Gesamt-PnL nur aus einem einzelnen guten Abschnitt '
                             'stammt -- trifft junge/duenne Coins besonders hart, da ihre kurze Historie in '
                             'noch kleinere, statistisch instabile Fenster zerfaellt. 1 = deaktiviert (altes '
                             'Verhalten).')
    args = parser.parse_args()

    CONFIG_SUFFIX = args.config_suffix
    MAX_DRAWDOWN_CONSTRAINT, MIN_WIN_RATE_CONSTRAINT, MIN_PNL_CONSTRAINT = args.max_drawdown / 100.0, args.min_win_rate, args.min_pnl
    START_CAPITAL, N_TRIALS, OPTIM_MODE = args.start_capital, args.trials, args.mode
    K_FOLDS = max(1, args.k_folds)
    MIN_TRADES_PER_YEAR = args.min_trades_per_year

    if args.pairs:
        # Paar-Modus: "AAVE:5m ETH:6h BTC:4h" → direkte Symbol/Timeframe-Zuordnung (kein Kreuzprodukt)
        TASKS = []
        for pair_str in args.pairs.split():
            sym, tf = pair_str.split(':', 1)
            TASKS.append({'symbol': f"{sym}/USDT:USDT", 'timeframe': tf})
    elif args.symbols and args.timeframes:
        symbols, timeframes = args.symbols.split(), args.timeframes.split()
        TASKS = [{'symbol': f"{s}/USDT:USDT", 'timeframe': tf} for s in symbols for tf in timeframes]
    else:
        print("FEHLER: Entweder --pairs oder --symbols + --timeframes muss angegeben werden.")
        sys.exit(1)

    # Run-level summary collector
    run_tasks_summary = []
    run_start_ts = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    for task in TASKS:
        symbol, timeframe = task['symbol'], task['timeframe']

        CURRENT_SYMBOL = symbol
        CURRENT_TIMEFRAME = timeframe

        print(f"\n===== Optimiere: {symbol} ({timeframe}) =====")
        # Per-Paar Lookback: wenn --start_date auto, berechne Startdatum je Timeframe
        if args.start_date.lower() == 'auto':
            pair_lookback = TF_LOOKBACK_DAYS.get(timeframe, 365)
            end_dt = datetime.strptime(args.end_date, '%Y-%m-%d')
            pair_start_date = (end_dt - timedelta(days=pair_lookback)).strftime('%Y-%m-%d')
            print(f"Datenbereich: {pair_lookback} Tage ({pair_start_date} bis {args.end_date})")
        else:
            pair_start_date = args.start_date
        HISTORICAL_DATA = load_data(symbol, timeframe, pair_start_date, args.end_date)

        fine_tf = FINE_TF_MAP.get(timeframe)
        FINE_DATA = LazyFineData(symbol, fine_tf) if fine_tf else None

        # Indikatoren einmalig vorberechnen — ATR/ADX/volume_ma sind trial-unabhängig
        # (adx_period=14 ist fix, volume_ma_period=20 ist fix)
        if not HISTORICAL_DATA.empty:
            import ta as _ta
            try:
                _atr = _ta.volatility.AverageTrueRange(
                    high=HISTORICAL_DATA['high'], low=HISTORICAL_DATA['low'],
                    close=HISTORICAL_DATA['close'], window=14)
                HISTORICAL_DATA['atr'] = _atr.average_true_range()
                _adx = _ta.trend.ADXIndicator(
                    high=HISTORICAL_DATA['high'], low=HISTORICAL_DATA['low'],
                    close=HISTORICAL_DATA['close'], window=14)
                HISTORICAL_DATA['adx']     = _adx.adx()
                HISTORICAL_DATA['adx_pos'] = _adx.adx_pos()
                HISTORICAL_DATA['adx_neg'] = _adx.adx_neg()
                HISTORICAL_DATA['volume_ma'] = HISTORICAL_DATA['volume'].rolling(window=20).mean()
                print(f"Indikatoren vorberechnet (ATR/ADX/volume_ma) — werden pro Trial wiederverwendet.")
            except Exception as _e:
                print(f"Warnung: Indikator-Vorberechnung fehlgeschlagen ({_e}), wird pro Trial berechnet.")

            # 70/30 Walk-Forward Split
            TRAIN_SPLIT_IDX = int(len(HISTORICAL_DATA) * 0.70)
            TRAIN_DATA = HISTORICAL_DATA.iloc[:TRAIN_SPLIT_IDX].copy()
            TEST_DATA  = HISTORICAL_DATA.iloc[TRAIN_SPLIT_IDX:].copy()
            train_from = TRAIN_DATA.index[0].strftime('%Y-%m-%d')
            train_to   = TRAIN_DATA.index[-1].strftime('%Y-%m-%d')
            test_from  = TEST_DATA.index[0].strftime('%Y-%m-%d')
            test_to    = TEST_DATA.index[-1].strftime('%Y-%m-%d')
            print(f"WFV-Split: Train={len(TRAIN_DATA)} Kerzen (70%) [{train_from} → {train_to}], Test={len(TEST_DATA)} Kerzen (30%) [{test_from} → {test_to}]")
            _train_days = max(1, (TRAIN_DATA.index[-1] - TRAIN_DATA.index[0]).days)
            _test_days  = max(1, (TEST_DATA.index[-1]  - TEST_DATA.index[0]).days)
            _min_tr = max(2, int(MIN_TRADES_PER_YEAR * _train_days / 365))
            _min_te = max(1, int(MIN_TRADES_PER_YEAR * _test_days  / 365))
            print(f"Mindest-Trades: Train >={_min_tr} ({_train_days}d @ {MIN_TRADES_PER_YEAR}/Jahr), Test >={_min_te} ({_test_days}d)")
            if K_FOLDS > 1:
                print(f"K-Fold-Robustheit: Train in {K_FOLDS} Teilfenster gesplittet, Score nutzt das schlechteste ({_train_days // K_FOLDS}d je Fenster)")

        if HISTORICAL_DATA.empty:
            print("Keine Daten geladen. Überspringe.")
            run_tasks_summary.append({'symbol': symbol, 'timeframe': timeframe, 'status': 'no_data'})
            continue

        print("\n--- Bewertung der Datensatz-Qualität ---")
        evaluation = evaluate_dataset(HISTORICAL_DATA.copy(), timeframe)
        print(f"Note: {evaluation['score']} / 10\n" + "\n".join(evaluation['justification']) + "\n----------------------------------------")
        if evaluation['score'] < 3:
            print(f"Datensatz-Qualität zu gering. Überspringe Optimierung.")
            run_tasks_summary.append({'symbol': symbol, 'timeframe': timeframe, 'status': 'bad_data', 'score': evaluation['score']})
            continue

        DB_FILE = os.path.join(PROJECT_ROOT, 'artifacts', 'db', 'optuna_studies_smc.db')
        os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
        STORAGE_URL = f"sqlite:///{DB_FILE}?timeout=60"
        study_name = f"smc_{create_safe_filename(symbol, timeframe)}{CONFIG_SUFFIX}_{OPTIM_MODE}"

        study = optuna.create_study(storage=STORAGE_URL, study_name=study_name, direction="maximize", load_if_exists=True)
        _seed_from_previous_study(study, symbol, timeframe, STORAGE_URL)

        # --- Progress reporting callback (writes progress log + status JSON) ---
        import time, pathlib
        LOGS_DIR = os.path.join(PROJECT_ROOT, 'logs')
        os.makedirs(LOGS_DIR, exist_ok=True)
        PROGRESS_LOG = os.path.join(LOGS_DIR, 'optimizer_output.log')
        # Ensure the log file always exists (create if missing)
        if not os.path.exists(PROGRESS_LOG):
            with open(PROGRESS_LOG, 'w', encoding='utf-8') as pf:
                pf.write("")
        STATUS_FILE = os.path.join(PROJECT_ROOT, 'data', 'cache', '.optimization_status.json')

        def _write_progress_line(line: str):
            ts = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
            try:
                with open(PROGRESS_LOG, 'a', encoding='utf-8') as pf:
                    pf.write(f"{ts} {line}\n")
            except Exception:
                pass

        def _write_status_json(status: dict):
            try:
                os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
                with open(STATUS_FILE, 'w', encoding="utf-8") as sf:
                    json.dump(status, sf, indent=2)
            except Exception:
                pass

        start_time = time.time()
        _last_bar_time = [0.0]  # Throttle für ASCII-Fortschrittsbalken
        _bar_final_printed = [False]  # verhindert Doppeldruck der letzten Zeile (parallele Jobs)
        _trials_at_start = [0]  # Anzahl der Trials im DB vor diesem Run
        _max_test_pnl = [None]  # Monoton steigendes Maximum des Test-PnL (für Anzeige)

        def _trial_callback(study_obj, trial_obj):
            # Called after each trial (including pruned/complete)
            try:
                trials_done = min(
                    len([t for t in study_obj.trials if t.state != optuna.trial.TrialState.RUNNING]) - _trials_at_start[0],
                    N_TRIALS
                )
                trials_total = N_TRIALS
                best = None
                best_test_pnl_cb = None
                try:
                    best = study_obj.best_trial
                    best_val = round(best.value, 4) if best and best.value is not None else None
                    best_no = best.number if best else None
                    best_test_pnl_cb = best.user_attrs.get('test_pnl') if best else None
                    # Aktuellen Trial ebenfalls prüfen (kann höheren test_pnl haben als best_trial)
                    cur_pnl = trial_obj.user_attrs.get('test_pnl') if trial_obj else None
                    for pnl in (best_test_pnl_cb, cur_pnl):
                        if pnl is not None:
                            if _max_test_pnl[0] is None or pnl > _max_test_pnl[0]:
                                _max_test_pnl[0] = pnl
                except Exception:
                    best_val = None
                    best_no = None

                elapsed = int(time.time() - start_time)
                line = f"PROGRESS symbol={CURRENT_SYMBOL} timeframe={CURRENT_TIMEFRAME} trials={trials_done}/{trials_total} best_test_pnl={best_test_pnl_cb} best_trial={best_no} elapsed_s={elapsed}"
                _write_progress_line(line)

                status = {
                    'status': 'running',
                    'symbol': CURRENT_SYMBOL,
                    'timeframe': CURRENT_TIMEFRAME,
                    'trials_done': trials_done,
                    'trials_total': trials_total,
                    'best_value': best_val,
                    'best_test_pnl': best_test_pnl_cb,
                    'best_trial_no': best_no,
                    'started_at': datetime.fromtimestamp(start_time, timezone.utc).isoformat().replace('+00:00', 'Z'),
                    'last_update': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                }
                _write_status_json(status)

                # ASCII-Fortschrittsbalken (alle 5 Sek. oder beim letzten Trial)
                now_t = time.time()
                is_done = trials_done >= trials_total
                if is_done and _bar_final_printed[0]:
                    return  # parallele Jobs: finalen Druck nur einmal
                if now_t - _last_bar_time[0] >= 5.0 or is_done:
                    _last_bar_time[0] = now_t
                    bar_width = 25
                    pct = min(trials_done / trials_total, 1.0) if trials_total > 0 else 0
                    filled = int(bar_width * pct)
                    bar = '█' * filled + '░' * (bar_width - filled)
                    sym_short = CURRENT_SYMBOL.split('/')[0]
                    best_str = f"{_max_test_pnl[0]:+.2f}%" if _max_test_pnl[0] is not None else "---"
                    line = f"  [{bar}] {sym_short}/{CURRENT_TIMEFRAME}  {trials_done:>4}/{trials_total}  ({pct*100:5.1f}%)  Best Test-PnL: {best_str}  {elapsed}s"
                    # \r überschreibt dieselbe Zeile; Leerzeichen am Ende löschen Reste
                    print(f"\r{line:<80}", end='\n' if is_done else '', flush=True)
                    if is_done:
                        _bar_final_printed[0] = True
            except Exception:
                pass

        _trials_at_start[0] = len([t for t in study.trials if t.state != optuna.trial.TrialState.RUNNING])
        try:
            # catch=(Exception,): eine einzelne fehlschlagende Trial (Race Condition o.ae.)
            # markiert Optuna nur diese Trial als FAILED und macht mit den restlichen
            # weiter, statt das komplette n_trials-Budget fuer dieses Paar wegzuwerfen.
            # Ohne das killte 2026-08-28 ein einzelner Fehler (Trial 7 von 200) den
            # gesamten restlichen Lauf fuer ADA/1h.
            study.optimize(objective, n_trials=N_TRIALS, n_jobs=args.jobs, callbacks=[_trial_callback],
                          show_progress_bar=False, catch=(Exception,))
        except Exception as e_opt:
            print(f"FEHLER während Optuna optimize: {e_opt}")
            # mark status file as error for visibility
            _write_progress_line(f"ERROR symbol={CURRENT_SYMBOL} timeframe={CURRENT_TIMEFRAME} error={e_opt}")
            try:
                _write_status_json({'status': 'error', 'error': str(e_opt), 'last_update': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')})
            except Exception:
                pass
            continue # Nächsten Task versuchen

        # Beide SMC-Caches nach jedem Task leeren (neues Symbol/Timeframe = andere Daten)
        with _SMC_TRAIN_CACHE_LOCK:
            _SMC_TRAIN_CACHE.clear()
        with _SMC_TEST_CACHE_LOCK:
            _SMC_TEST_CACHE.clear()

        valid_trials = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
        if not valid_trials:
            print(f"\n❌ FEHLER: Für {symbol} ({timeframe}) konnte keine Konfiguration gefunden werden.")
            run_tasks_summary.append({'symbol': symbol, 'timeframe': timeframe, 'status': 'no_valid_trials'})
            continue

        # Trainieren-dann-validieren statt Trainieren-waehrend-des-Validierens: Trials
        # werden REIN nach Train-Score gerankt (t.value enthaelt seit dem Fix kein
        # Test-Signal mehr), dann wird das ERSTE gewaehlt, das sich auch auf dem nie
        # gesehenen Test-Fenster bestaetigt. Das Test-Fenster beeinflusst also nur noch
        # EINMALIG die finale Auswahl unter den (train-blind gefundenen) Kandidaten,
        # nicht mehr die Suche selbst.
        ranked_by_train = sorted(valid_trials, key=lambda t: t.value, reverse=True)
        best_trial = None
        for t in ranked_by_train:
            # Erst muss der Trial selbst auf dem Trainingsfenster profitabel sein --
            # sonst kann ein beim Training eigentlich verlustreicher Trial durch reines
            # Durchprobieren vieler Kandidaten "gerettet" werden, weil er zufaellig auf
            # dem Test-Fenster gut aussieht (Rest-Leckage: 200 Kandidaten nacheinander
            # gegen das Test-Fenster zu pruefen ist selbst eine Form von Data-Snooping,
            # auch wenn keiner davon direkt fuer Test optimiert wurde). Konkret
            # beobachtet: SOL/2h waehlte sonst train=-14.9% / test=+20.1% -- ein Trial,
            # der beim Training Geld verliert, ist kein belastbarer Fund, egal wie gut
            # er zufaellig auf dem Testfenster abschneidet.
            # robust_train_pnl (schlechtestes K-Fold-Teilfenster) statt der rohen
            # Gesamt-Train-PnL -- sonst kann ein Trial mit nur knapp positiver
            # Gesamt-PnL aber katastrophalem Einzel-Fenster (z.B. SNDK/1h Trial 0:
            # train_pnl=+0.48%, robust=-11.43%) trotzdem durchrutschen, wenn sein
            # Test-Fenster zufaellig gut aussieht -- exakt dieselbe Rest-Leckage wie
            # beim SOL/2h-Fund oben, nur dass train_pnl selbst sie nicht mehr faengt.
            t_train_pnl = t.user_attrs.get('robust_train_pnl', t.user_attrs.get('train_pnl', -1e9))
            if t_train_pnl <= 0:
                continue
            t_test_trades = t.user_attrs.get('test_trades', 0)
            t_min_test    = t.user_attrs.get('min_test_trades', 1)
            t_test_pnl    = t.user_attrs.get('test_pnl', -1e9)
            t_test_wr     = t.user_attrs.get('test_wr', 0)
            t_test_dd_pct = t.user_attrs.get('test_dd_pct', 1000.0)
            if t_test_trades < t_min_test:
                continue  # zu wenige Test-Trades fuer eine belastbare Aussage
            if t_test_dd_pct > MAX_DRAWDOWN_CONSTRAINT * 100:
                continue
            if t_test_pnl <= 0:
                continue
            if OPTIM_MODE == "strict" and (t_test_wr < MIN_WIN_RATE_CONSTRAINT or t_test_pnl < MIN_PNL_CONSTRAINT):
                continue
            best_trial = t
            break

        if best_trial is None:
            print(f"\n❌ Kein Trial (von {len(valid_trials)} trainierten) bestätigte sich auf dem "
                  f"nie gesehenen Test-Fenster für {symbol} ({timeframe}).")
            run_tasks_summary.append({'symbol': symbol, 'timeframe': timeframe,
                                      'status': 'no_test_confirmation', 'n_trained': len(valid_trials)})
            continue

        best_params = best_trial.params

        config_dir = os.path.join(PROJECT_ROOT, 'src', 'titanbot', 'strategy', 'configs')
        os.makedirs(config_dir, exist_ok=True)
        config_output_path = os.path.join(config_dir, f'config_{create_safe_filename(symbol, timeframe)}{CONFIG_SUFFIX}.json')

        strategy_config = {
            'swingsLength': best_params['swingsLength'],
            'ob_mitigation': best_params['ob_mitigation'],
            'use_adx_filter': best_params['use_adx_filter'],
            'adx_period': best_params.get('adx_period', 14),
            'adx_threshold': best_params.get('adx_threshold', 25),
            'use_pd_filter': best_params.get('use_pd_filter', True),
            'use_liquidity_sweep_filter': best_params.get('use_liquidity_sweep_filter', True),
            'liquidity_lookback': best_params.get('liquidity_lookback', 20),
            'min_fvg_size_pct': round(best_params.get('min_fvg_size_pct', 0.05), 4),
            'min_ob_quality': round(best_params.get('min_ob_quality', 0.2), 3),
            'max_ob_touches': best_params.get('max_ob_touches', 1),
            'use_rejection_candle': best_params.get('use_rejection_candle', True),
            'use_mtf_filter': best_params.get('use_mtf_filter', False),
            'use_entry_confirmation': best_params.get('use_entry_confirmation', True),
            'use_swing_ob': best_params.get('use_swing_ob', True),
            'use_zone_based_tp': best_params.get('use_zone_based_tp', False),
            'use_momentum_filter': best_params.get('use_momentum_filter', False),
            'momentum_lookback': best_params.get('momentum_lookback', 3),
            'momentum_require_both': best_params.get('momentum_require_both', True),
            'volume_ma_period': 20,
        }

        risk_config = {
            'margin_mode': "isolated",
            'risk_reward_ratio': round(best_params['risk_reward_ratio'], 2),
            'min_leverage': best_params['min_leverage'],
            'max_leverage': best_params['max_leverage'],
            'atr_multiplier_sl': round(best_params['atr_multiplier_sl'], 3),
            'min_sl_pct': 0.5,
            'structure_sl_buffer_pct': 0.2,
            'use_trailing_stop': best_params.get('use_trailing_stop', False),
            'trailing_stop_activation_rr': round(best_params.get('trailing_stop_activation_rr', 1.5), 2),
            'trailing_stop_callback_rate_pct': round(best_params.get('trailing_stop_callback_rate_pct', 1.0), 2),
        }
        behavior_config = {"use_longs": True, "use_shorts": True}
        
        # Extrahiere WFV-Metriken aus best_trial user_attrs
        best_test_pnl    = best_trial.user_attrs.get('test_pnl',    None)
        best_train_pnl   = best_trial.user_attrs.get('train_pnl',   None)
        best_test_wr     = best_trial.user_attrs.get('test_wr',     None)
        best_test_trades = best_trial.user_attrs.get('test_trades',  None)
        best_test_dd_pct = best_trial.user_attrs.get('test_dd_pct', None)

        config_output = {
            "market": {"symbol": symbol, "timeframe": timeframe},
            "strategy": strategy_config,
            "risk": risk_config,
            "behavior": behavior_config,
            "_meta": {
                "wfv": "70/30",
                "test_pnl_pct":    best_test_pnl,
                "train_pnl_pct":   best_train_pnl,
                "test_wr":         best_test_wr,
                "test_trades":     best_test_trades,
                "test_dd_pct":     best_test_dd_pct,
                "composite_score": round(best_trial.value, 4) if best_trial.value is not None else None,
                "optimized_at":    datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            }
        }

        # --- Smart-save: überschreibe nur, wenn die neue Konfiguration besser ist als die gespeicherte ---
        history_dir = os.path.join(PROJECT_ROOT, 'artifacts', 'results')
        os.makedirs(history_dir, exist_ok=True)
        history_path = os.path.join(history_dir, 'optimizer_history.json')

        key = create_safe_filename(symbol, timeframe)
        existing_best = None
        try:
            if os.path.exists(history_path):
                with open(history_path, 'r', encoding='utf-8') as hf:
                    history = json.load(hf)
                existing_best = history.get(key, {}).get('best_pnl')
        except Exception:
            existing_best = None

        saved = False
        status = 'saved'
        config_missing = not os.path.exists(config_output_path)
        new_test_pnl = best_test_pnl if best_test_pnl is not None else -9999

        # Quality gate: nur speichern wenn OOS PnL positiv ist
        if new_test_pnl <= 0:
            print(f"\n❌ Kein profitables OOS-Ergebnis für {symbol} ({timeframe}) — Test-PnL: {new_test_pnl:.2f}%. Config wird NICHT gespeichert.")
            run_tasks_summary.append({'symbol': symbol, 'timeframe': timeframe, 'status': 'quality_gate_failed', 'test_pnl': new_test_pnl})
            continue

        if existing_best is None or config_missing or new_test_pnl > existing_best:
            # besser — schreibe die Config und aktualisiere die Historie
            try:
                with open(config_output_path, 'w', encoding='utf-8') as f:
                    json.dump(config_output, f, indent=4)
                saved = True
                status = 'new_best'
                # update history
                try:
                    hist = {}
                    if os.path.exists(history_path):
                        with open(history_path, 'r', encoding='utf-8') as hf:
                            hist = json.load(hf)
                    hist[key] = {
                        'best_pnl': new_test_pnl,
                        'updated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                        'config': os.path.relpath(config_output_path, PROJECT_ROOT)
                    }
                    with open(history_path, 'w', encoding='utf-8') as hf:
                        json.dump(hist, hf, indent=2)
                except Exception:
                    pass
                pnl_str = f"{new_test_pnl:.2f}%" if new_test_pnl != -9999 else "n/a"
                print(f"\n✔ Beste Konfiguration (Test-PnL: {pnl_str}) wurde in '{config_output_path}' gespeichert.")
            except Exception as e:
                print(f"Fehler beim Speichern der Config: {e}")
                status = 'save_error'
        else:
            # schlechteres oder gleiches Ergebnis – NICHT überschreiben
            saved = False
            status = 'unchanged'
            print(f"\nℹ️ Gefundene Konfiguration (Test-PnL: {new_test_pnl:.2f}%) ist schlechter/gleich als vorhandene (Test-PnL: {existing_best}). Überschreibe nicht.")

        # Sammle Task-Level Summary für das ganze Run-Report
        run_tasks_summary.append({
            'symbol': symbol,
            'timeframe': timeframe,
            'test_pnl': best_test_pnl,
            'train_pnl': best_train_pnl,
            'test_wr': best_test_wr,
            'test_trades': best_test_trades,
            'test_dd_pct': best_test_dd_pct,
            'composite_score': round(best_trial.value, 4) if best_trial.value is not None else None,
            'saved': saved,
            'status': status,
            'config_path': os.path.relpath(config_output_path, PROJECT_ROOT)
        })


    # --- Schreibe Run‑Summary in artifacts/results/last_optimizer_run.json (kurz und maschinenlesbar) ---
    try:
        if args.results_file:
            summary_path = args.results_file
            os.makedirs(os.path.dirname(summary_path) or '.', exist_ok=True)
        else:
            results_dir = os.path.join(PROJECT_ROOT, 'artifacts', 'results')
            os.makedirs(results_dir, exist_ok=True)
            summary_path = os.path.join(results_dir, 'last_optimizer_run.json')
        run_end_ts = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        summary = {
            'start_time': run_start_ts,
            'end_time': run_end_ts,
            'duration_s': int(time.time() - start_time),
            'tasks': run_tasks_summary
        }
        with open(summary_path, 'w', encoding='utf-8') as sf:
            json.dump(summary, sf, indent=2)
        print(f"\n✔ Run‑Summary geschrieben nach '{summary_path}'")
    except Exception as _:
        pass


if __name__ == "__main__":
    main()
