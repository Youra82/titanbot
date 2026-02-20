# /root/titanbot/src/titanbot/analysis/optimizer.py (Leverage BEGRENZT auf 5-15, mit MTF-HTF-Speicherung)
import os
import sys
import json
import optuna
import numpy as np
import argparse
import logging
import warnings
from datetime import datetime, timezone

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

HISTORICAL_DATA = None
CURRENT_HTF_DATA = None  # Pre-loaded HTF data — einmal laden, nicht pro Trial
CURRENT_SYMBOL = None # NEU: Globale Variable für Symbol (wird für Backtester benötigt)
CURRENT_TIMEFRAME = None
CURRENT_HTF = None # NEU: Globale Variable für den berechneten HTF
CONFIG_SUFFIX = ""
MAX_DRAWDOWN_CONSTRAINT = 0.30
MIN_WIN_RATE_CONSTRAINT = 55.0
MIN_PNL_CONSTRAINT = 0.0
START_CAPITAL = 1000
OPTIM_MODE = "strict"

def create_safe_filename(symbol, timeframe):
    return f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"

def objective(trial):
    smc_params = {
        'swingsLength': trial.suggest_int('swingsLength', 10, 100),
        'ob_mitigation': trial.suggest_categorical('ob_mitigation', ['High/Low', 'Close']),
        'use_adx_filter': trial.suggest_categorical('use_adx_filter', [True, False]),
        'adx_period': trial.suggest_int('adx_period', 10, 20),
        'adx_threshold': trial.suggest_int('adx_threshold', 20, 30),
        'symbol': CURRENT_SYMBOL, # NEU: Füge Symbol und Timeframe hinzu
        'timeframe': CURRENT_TIMEFRAME,
        'htf': CURRENT_HTF, # NEU: Füge den HTF hinzu
        'htf_data': CURRENT_HTF_DATA  # Pre-loaded HTF data (verhindert parallele Cache-Korruption)
    }
    risk_params = {
        'risk_reward_ratio': trial.suggest_float('risk_reward_ratio', 1.0, 5.0),
        'risk_per_trade_pct': trial.suggest_float('risk_per_trade_pct', 0.5, 2.0),
        'leverage': trial.suggest_int('leverage', 5, 15), # Leverage zwischen 5x und 15x
        'trailing_stop_activation_rr': trial.suggest_float('trailing_stop_activation_rr', 1.0, 4.0),
        'trailing_stop_callback_rate_pct': trial.suggest_float('trailing_stop_callback_rate_pct', 0.5, 3.0),
        'atr_multiplier_sl': trial.suggest_float('atr_multiplier_sl', 1.0, 4.0),
        'min_sl_pct': trial.suggest_float('min_sl_pct', 0.3, 2.0) # Als % (0.3% bis 2.0%)
    }

    # Übergebe BEIDE Parameter-Dictionaries an den Backtester
    result = run_smc_backtest( HISTORICAL_DATA.copy(), smc_params, risk_params, START_CAPITAL, verbose=False )
    pnl = result.get('total_pnl_pct', -1000)
    drawdown = result.get('max_drawdown_pct', 1.0) # Backtester gibt Dezimal zurück
    trades = result.get('trades_count', 0)
    win_rate = result.get('win_rate', 0)

    # Pruning
    if OPTIM_MODE == "strict" and (
        drawdown > MAX_DRAWDOWN_CONSTRAINT or win_rate < MIN_WIN_RATE_CONSTRAINT or
        pnl < MIN_PNL_CONSTRAINT or trades < 50):
        raise optuna.exceptions.TrialPruned()
    elif OPTIM_MODE == "best_profit" and (
        drawdown > MAX_DRAWDOWN_CONSTRAINT or trades < 50):
        raise optuna.exceptions.TrialPruned()

    return pnl

def main():
    global HISTORICAL_DATA, CURRENT_HTF_DATA, CURRENT_SYMBOL, CURRENT_TIMEFRAME, CURRENT_HTF, CONFIG_SUFFIX, MAX_DRAWDOWN_CONSTRAINT, MIN_WIN_RATE_CONSTRAINT, MIN_PNL_CONSTRAINT, START_CAPITAL, OPTIM_MODE
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
        HISTORICAL_DATA = load_data(symbol, timeframe, args.start_date, args.end_date)
        # HTF-Daten einmalig laden (vor study.optimize) — verhindert parallele Cache-Korruption
        if CURRENT_HTF and CURRENT_HTF != timeframe:
            print(f"Lade HTF-Daten ({CURRENT_HTF}) einmalig vor der Optimierung...")
            CURRENT_HTF_DATA = load_data(symbol, CURRENT_HTF, args.start_date, args.end_date)
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
        _trials_at_start = [0]  # Anzahl der Trials im DB vor diesem Run

        def _trial_callback(study_obj, trial_obj):
            # Called after each trial (including pruned/complete)
            try:
                trials_done = min(
                    len([t for t in study_obj.trials if t.state != optuna.trial.TrialState.RUNNING]) - _trials_at_start[0],
                    N_TRIALS
                )
                trials_total = N_TRIALS
                best = None
                try:
                    best = study_obj.best_trial
                    best_val = round(best.value, 2) if best and best.value is not None else None
                    best_no = best.number if best else None
                except Exception:
                    best_val = None
                    best_no = None

                elapsed = int(time.time() - start_time)
                line = f"PROGRESS symbol={CURRENT_SYMBOL} timeframe={CURRENT_TIMEFRAME} trials={trials_done}/{trials_total} best_pnl={best_val} best_trial={best_no} elapsed_s={elapsed}"
                _write_progress_line(line)

                status = {
                    'status': 'running',
                    'symbol': CURRENT_SYMBOL,
                    'timeframe': CURRENT_TIMEFRAME,
                    'trials_done': trials_done,
                    'trials_total': trials_total,
                    'best_value': best_val,
                    'best_trial_no': best_no,
                    'started_at': datetime.fromtimestamp(start_time, timezone.utc).isoformat().replace('+00:00', 'Z'),
                    'last_update': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                }
                _write_status_json(status)

                # ASCII-Fortschrittsbalken (alle 5 Sek. oder beim letzten Trial)
                now_t = time.time()
                if now_t - _last_bar_time[0] >= 5.0 or trials_done >= trials_total:
                    _last_bar_time[0] = now_t
                    bar_width = 25
                    pct = min(trials_done / trials_total, 1.0) if trials_total > 0 else 0
                    filled = int(bar_width * pct)
                    bar = '█' * filled + '░' * (bar_width - filled)
                    sym_short = CURRENT_SYMBOL.split('/')[0]
                    best_str = f"{best_val:+.2f}%" if best_val is not None else "---"
                    print(f"  [{bar}] {sym_short}/{CURRENT_TIMEFRAME}  {trials_done:>4}/{trials_total}  ({pct*100:5.1f}%)  Best: {best_str}  {elapsed}s", flush=True)
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

        valid_trials = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
        if not valid_trials: print(f"\n❌ FEHLER: Für {symbol} ({timeframe}) konnte keine Konfiguration gefunden werden."); continue

        best_trial = max(valid_trials, key=lambda t: t.value)
        best_params = best_trial.params

        config_dir = os.path.join(PROJECT_ROOT, 'src', 'titanbot', 'strategy', 'configs')
        os.makedirs(config_dir, exist_ok=True)
        config_output_path = os.path.join(config_dir, f'config_{create_safe_filename(symbol, timeframe)}{CONFIG_SUFFIX}.json')

        strategy_config = {
            'swingsLength': best_params['swingsLength'],
            'ob_mitigation': best_params['ob_mitigation'],
            'use_adx_filter': best_params['use_adx_filter'],
            'adx_period': best_params['adx_period'],
            'adx_threshold': best_params['adx_threshold']
        }
        
        risk_config = {
            'margin_mode': "isolated",
            'risk_per_trade_pct': round(best_params['risk_per_trade_pct'], 2),
            'risk_reward_ratio': round(best_params['risk_reward_ratio'], 2),
            'leverage': best_params['leverage'],
            'trailing_stop_activation_rr': round(best_params['trailing_stop_activation_rr'], 2),
            'trailing_stop_callback_rate_pct': round(best_params['trailing_stop_callback_rate_pct'], 2),
            'atr_multiplier_sl': round(best_params['atr_multiplier_sl'], 2),
            'min_sl_pct': round(best_params['min_sl_pct'], 2)
        }
        behavior_config = {"use_longs": True, "use_shorts": True}
        
        # NEU: Speichere HTF in der finalen Config
        config_output = {
            "market": {"symbol": symbol, "timeframe": timeframe, "htf": CURRENT_HTF}, 
            "strategy": strategy_config,
            "risk": risk_config, "behavior": behavior_config
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
        if existing_best is None or (best_trial.value is not None and best_trial.value > existing_best):
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
                    hist[key] = {'best_pnl': round(best_trial.value, 2) if best_trial.value is not None else None, 'updated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'), 'config': os.path.relpath(config_output_path, PROJECT_ROOT)}
                    with open(history_path, 'w', encoding='utf-8') as hf:
                        json.dump(hist, hf, indent=2)
                except Exception:
                    pass
                print(f"\n✔ Beste Konfiguration (PnL: {best_trial.value:.2f}%) wurde in '{config_output_path}' gespeichert.")
            except Exception as e:
                print(f"Fehler beim Speichern der Config: {e}")
                status = 'save_error'
        else:
            # schlechteres oder gleiches Ergebnis – NICHT überschreiben
            saved = False
            status = 'unchanged'
            print(f"\nℹ️ Gefundene Konfiguration (PnL: {best_trial.value:.2f}%) ist schlechter/gleich als vorhandene (PnL: {existing_best}). Überschreibe nicht.")

        # Sammle Task-Level Summary für das ganze Run-Report
        run_tasks_summary.append({
            'symbol': symbol,
            'timeframe': timeframe,
            'pnl': round(best_trial.value, 2) if best_trial.value is not None else None,
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
