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

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
logging.getLogger('tensorflow').setLevel(logging.ERROR)
logging.getLogger('absl').setLevel(logging.ERROR)
warnings.filterwarnings('ignore', category=UserWarning, module='keras')

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from titanbot.analysis.backtester import load_data, run_smc_backtest
from titanbot.analysis.evaluator import evaluate_dataset
from titanbot.utils.timeframe_utils import determine_htf # NEU: Import für HTF Bestimmung

optuna.logging.set_verbosity(optuna.logging.WARNING)

# Empfohlener Lookback je Timeframe (wenn --start_date auto übergeben wird)
TF_LOOKBACK_DAYS = {'5m': 60, '15m': 60, '30m': 365, '1h': 365,
                    '2h': 730, '4h': 730, '6h': 730, '1d': 1095}

import math
import threading as _threading

HISTORICAL_DATA = None
TRAIN_DATA      = None   # 70% — Optimierung
TEST_DATA       = None   # 30% — Out-of-Sample Validierung
TRAIN_SPLIT_IDX = 0
CURRENT_HTF_DATA = None
CURRENT_HTF_BIAS = None
CURRENT_SYMBOL = None
CURRENT_TIMEFRAME = None
CURRENT_HTF = None

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

def create_safe_filename(symbol, timeframe):
    return f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"

def _get_smc_precomputed(cache, cache_lock, data, smc_params):
    """SMC-Engine-Ergebnis aus Cache holen oder berechnen."""
    _cache_key = (smc_params['swingsLength'], smc_params['ob_mitigation'], smc_params['liquidity_lookback'])
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
        'use_pd_filter': trial.suggest_categorical('use_pd_filter', [True, False]),
        'use_liquidity_sweep_filter': trial.suggest_categorical('use_liquidity_sweep_filter', [True, False]),
        'liquidity_lookback': trial.suggest_categorical('liquidity_lookback', [10, 15, 20, 25]),
        'min_fvg_size_pct': trial.suggest_float('min_fvg_size_pct', 0.05, 0.20),
        'min_ob_quality': trial.suggest_float('min_ob_quality', 0.10, 0.50),
        'max_ob_touches': trial.suggest_int('max_ob_touches', 0, 2),
        'use_rejection_candle': trial.suggest_categorical('use_rejection_candle', [True, False]),
        'symbol': CURRENT_SYMBOL,
        'timeframe': CURRENT_TIMEFRAME,
        'htf': CURRENT_HTF,
        'htf_data': CURRENT_HTF_DATA,
        'htf_bias': CURRENT_HTF_BIAS,
    }
    risk_params = {
        'risk_reward_ratio': trial.suggest_float('risk_reward_ratio', 1.5, 4.0),
        'risk_per_trade_pct': trial.suggest_float('risk_per_trade_pct', 0.5, 2.0),
        'min_leverage': trial.suggest_int('min_leverage', 2, 8),
        'max_leverage': trial.suggest_int('max_leverage', 8, 30),
        'sl_buffer_atr_mult': trial.suggest_float('sl_buffer_atr_mult', 0.05, 0.5),
        'trailing_stop_activation_rr': trial.suggest_float('trailing_stop_activation_rr', 1.0, 3.5),
        'trailing_stop_callback_rate_pct': trial.suggest_float('trailing_stop_callback_rate_pct', 0.5, 2.5),
    }

    # ── STUFE 1: TRAIN-Backtest (70% der Daten) — leichtes Pruning ──────────
    smc_params['_precomputed_smc'] = _get_smc_precomputed(
        _SMC_TRAIN_CACHE, _SMC_TRAIN_CACHE_LOCK, TRAIN_DATA, smc_params)

    train_result = run_smc_backtest(TRAIN_DATA.copy(), smc_params, risk_params, START_CAPITAL, verbose=False)
    train_pnl    = train_result.get('total_pnl_pct', -1000)
    train_dd     = train_result.get('max_drawdown_pct', 1.0)
    train_trades = train_result.get('trades_count', 0)

    min_train_trades = max(2, len(TRAIN_DATA) // 300)
    if train_trades < min_train_trades or train_dd > MAX_DRAWDOWN_CONSTRAINT:
        raise optuna.exceptions.TrialPruned()

    # ── STUFE 2: TEST-Backtest (30% der Daten) — strenges Pruning ───────────
    smc_params['_precomputed_smc'] = _get_smc_precomputed(
        _SMC_TEST_CACHE, _SMC_TEST_CACHE_LOCK, TEST_DATA, smc_params)

    test_result  = run_smc_backtest(
        TEST_DATA.copy(), smc_params, risk_params, START_CAPITAL,
        verbose=False, bar_index_offset=TRAIN_SPLIT_IDX)
    test_pnl     = test_result.get('total_pnl_pct', -1000)
    test_dd      = test_result.get('max_drawdown_pct', 1.0)
    test_trades  = test_result.get('trades_count', 0)
    test_wr      = test_result.get('win_rate', 0)

    min_test_trades = max(2, len(TEST_DATA) // 300)
    if test_trades < min_test_trades or test_dd > MAX_DRAWDOWN_CONSTRAINT:
        raise optuna.exceptions.TrialPruned()
    # strict: pnl>0 + win_rate + min_pnl zwingend
    # best_profit: pnl>0 wird nicht erzwungen — Composite Score bestraft negative PnL indirekt
    if OPTIM_MODE == "strict":
        if test_pnl <= 0 or test_wr < MIN_WIN_RATE_CONSTRAINT or test_pnl < MIN_PNL_CONSTRAINT:
            raise optuna.exceptions.TrialPruned()

    # ── Kombinierter Score (von jaegerbot inspiriert) ────────────────────────
    # log1p komprimiert extreme Ausreißer; DD im Nenner bestraft Risiko
    train_score = math.log1p(max(0, train_pnl)) / max(train_dd * 100, 1.0)
    test_score  = math.log1p(max(0, test_pnl))  / max(test_dd  * 100, 1.0)
    trade_bonus = math.log1p(test_trades) * 4.0          # mehr Trades = mehr Compounding (stärker gewichtet)
    wr_bonus    = max(0.0, (test_wr - 40.0) / 10.0)      # Bonus ab 40% Win-Rate

    final_score = train_score * 0.30 + test_score * 0.70 + trade_bonus + wr_bonus

    # User-Attribute für Config-Export und Fortschrittsanzeige
    trial.set_user_attr('test_pnl',    round(test_pnl,    2))
    trial.set_user_attr('train_pnl',   round(train_pnl,   2))
    trial.set_user_attr('test_wr',     round(test_wr,     2))
    trial.set_user_attr('test_trades', test_trades)
    trial.set_user_attr('test_dd_pct', round(test_dd * 100, 2))

    return final_score

def main():
    global HISTORICAL_DATA, TRAIN_DATA, TEST_DATA, TRAIN_SPLIT_IDX, CURRENT_HTF_DATA, CURRENT_HTF_BIAS, CURRENT_SYMBOL, CURRENT_TIMEFRAME, CURRENT_HTF, CONFIG_SUFFIX, MAX_DRAWDOWN_CONSTRAINT, MIN_WIN_RATE_CONSTRAINT, MIN_PNL_CONSTRAINT, START_CAPITAL, OPTIM_MODE
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
    args = parser.parse_args()

    CONFIG_SUFFIX = args.config_suffix
    MAX_DRAWDOWN_CONSTRAINT, MIN_WIN_RATE_CONSTRAINT, MIN_PNL_CONSTRAINT = args.max_drawdown / 100.0, args.min_win_rate, args.min_pnl
    START_CAPITAL, N_TRIALS, OPTIM_MODE = args.start_capital, args.trials, args.mode

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

        # NEU: Globale Variablen setzen
        CURRENT_SYMBOL = symbol
        CURRENT_TIMEFRAME = timeframe
        CURRENT_HTF = determine_htf(timeframe)

        print(f"\n===== Optimiere: {symbol} ({timeframe}) | MTF-Bias von {CURRENT_HTF} =====")
        # Per-Paar Lookback: wenn --start_date auto, berechne Startdatum je Timeframe
        if args.start_date.lower() == 'auto':
            pair_lookback = TF_LOOKBACK_DAYS.get(timeframe, 365)
            end_dt = datetime.strptime(args.end_date, '%Y-%m-%d')
            pair_start_date = (end_dt - timedelta(days=pair_lookback)).strftime('%Y-%m-%d')
            print(f"Datenbereich: {pair_lookback} Tage ({pair_start_date} bis {args.end_date})")
        else:
            pair_start_date = args.start_date
        HISTORICAL_DATA = load_data(symbol, timeframe, pair_start_date, args.end_date)

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
            print(f"WFV-Split: Train={len(TRAIN_DATA)} Kerzen (70%), Test={len(TEST_DATA)} Kerzen (30%)")

        # HTF-Daten + Bias einmalig berechnen (vor study.optimize)
        # market_bias ist für alle Trials identisch → einmal reicht
        CURRENT_HTF_BIAS = None
        if CURRENT_HTF and CURRENT_HTF != timeframe:
            print(f"Lade HTF-Daten ({CURRENT_HTF}) und berechne Bias einmalig...")
            CURRENT_HTF_DATA = load_data(symbol, CURRENT_HTF, pair_start_date, args.end_date)
            if not CURRENT_HTF_DATA.empty:
                from titanbot.strategy.smc_engine import SMCEngine
                from titanbot.strategy.smc_engine import Bias
                _htf_engine = SMCEngine(settings={'swingsLength': 50, 'ob_mitigation': 'Close'})
                _htf_engine.process_dataframe(CURRENT_HTF_DATA[['open', 'high', 'low', 'close']].copy())
                CURRENT_HTF_BIAS = _htf_engine.swingTrend
                print(f"HTF-Swing-Bias ({CURRENT_HTF}): {CURRENT_HTF_BIAS.name}")
        else:
            CURRENT_HTF_DATA = None
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
            study.optimize(objective, n_trials=N_TRIALS, n_jobs=args.jobs, callbacks=[_trial_callback], show_progress_bar=False)
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

        best_trial = max(valid_trials, key=lambda t: t.value)
        best_params = best_trial.params

        config_dir = os.path.join(PROJECT_ROOT, 'src', 'titanbot', 'strategy', 'configs')
        os.makedirs(config_dir, exist_ok=True)
        config_output_path = os.path.join(config_dir, f'config_{create_safe_filename(symbol, timeframe)}{CONFIG_SUFFIX}.json')

        strategy_config = {
            'swingsLength': best_params['swingsLength'],
            'ob_mitigation': best_params['ob_mitigation'],
            'use_adx_filter': best_params['use_adx_filter'],
            'adx_period': best_params.get('adx_period', 14),
            'adx_threshold': best_params.get('adx_threshold', 25)
        }
        
        risk_config = {
            'margin_mode': "isolated",
            'risk_per_trade_pct': round(best_params['risk_per_trade_pct'], 2),
            'risk_reward_ratio': round(best_params['risk_reward_ratio'], 2),
            'min_leverage': best_params['min_leverage'],
            'max_leverage': best_params['max_leverage'],
            'sl_buffer_atr_mult': round(best_params['sl_buffer_atr_mult'], 3),
            'trailing_stop_activation_rr': round(best_params['trailing_stop_activation_rr'], 2),
            'trailing_stop_callback_rate_pct': round(best_params['trailing_stop_callback_rate_pct'], 2)
        }
        behavior_config = {"use_longs": True, "use_shorts": True}
        
        # Extrahiere WFV-Metriken aus best_trial user_attrs
        best_test_pnl    = best_trial.user_attrs.get('test_pnl',    None)
        best_train_pnl   = best_trial.user_attrs.get('train_pnl',   None)
        best_test_wr     = best_trial.user_attrs.get('test_wr',     None)
        best_test_trades = best_trial.user_attrs.get('test_trades',  None)
        best_test_dd_pct = best_trial.user_attrs.get('test_dd_pct', None)

        # NEU: Speichere HTF in der finalen Config + WFV-Meta
        config_output = {
            "market": {"symbol": symbol, "timeframe": timeframe, "htf": CURRENT_HTF},
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
