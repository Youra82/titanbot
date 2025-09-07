# code/analysis/local_refiner_optuna.py

import json
import os
import sys
import argparse
import optuna
import numpy as np
import time
import pandas as pd

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from analysis.backtest import run_envelope_backtest
from utilities.strategy_logic import calculate_envelope_indicators
from analysis.global_optimizer_pymoo import load_data, format_time

optuna.logging.set_verbosity(optuna.logging.WARNING)

HISTORICAL_DATA = None
START_CAPITAL = 1000.0
BASE_PARAMS = {}

def objective(trial):
    base_avg_period = BASE_PARAMS.get('average_period', 20)
    base_sl_pct = BASE_PARAMS.get('stop_loss_pct', 2.0)
    base_leverage = BASE_PARAMS.get('base_leverage', 10)
    base_target_atr = BASE_PARAMS.get('target_atr_pct', 2.0)
    base_envelopes = BASE_PARAMS.get('envelopes_pct', [5.0, 10.0])

    params = {
        'average_type': BASE_PARAMS['average_type'],
        'average_period': trial.suggest_int('average_period', max(5, base_avg_period - 10), base_avg_period + 10),
        'stop_loss_pct': trial.suggest_float('stop_loss_pct', max(0.5, base_sl_pct * 0.8), base_sl_pct * 1.2, log=True),
        'base_leverage': trial.suggest_int('base_leverage', max(1, base_leverage - 5), base_leverage + 5),
        'target_atr_pct': trial.suggest_float('target_atr_pct', max(0.5, base_target_atr * 0.8), base_target_atr * 1.2, log=True),
        'start_capital': START_CAPITAL,
        'max_leverage': 50.0
    }
    
    if not base_envelopes: return -float('inf')
    
    env_start = trial.suggest_float('env_start', max(0.5, base_envelopes[0] * 0.8), base_envelopes[0] * 1.2, log=True)
    
    if len(base_envelopes) > 1:
        step = base_envelopes[1] - base_envelopes[0]
        env_step = trial.suggest_float('env_step', max(0.5, step * 0.8), step * 1.2, log=True)
        params['envelopes_pct'] = [round(env_start + i * env_step, 2) for i in range(len(base_envelopes))]
    else:
        params['envelopes_pct'] = [round(env_start, 2)]

    data_with_indicators = calculate_envelope_indicators(HISTORICAL_DATA.copy(), params)
    result = run_envelope_backtest(data_with_indicators.dropna(), params)

    pnl = result.get('total_pnl_pct', -1000)
    drawdown = result.get('max_drawdown_pct', 1.0)
    
    score = pnl * (1 - drawdown)
    return score if np.isfinite(score) else -float('inf')

def main(n_jobs, n_trials):
    print("\n--- [Stufe 2/2] Lokale Verfeinerung mit Optuna ---")
    
    input_file = os.path.join(os.path.dirname(__file__), 'optimization_candidates.json')
    if not os.path.exists(input_file):
        print(f"Fehler: '{input_file}' nicht gefunden.")
        return

    with open(input_file, 'r') as f: candidates = json.load(f)
    print(f"Lade {len(candidates)} Kandidaten zur Verfeinerung...")
    
    if candidates:
        print("\nFühre kurzen Benchmark zur Zeitschätzung durch...")
        first_candidate = candidates[0]
        global HISTORICAL_DATA, BASE_PARAMS, START_CAPITAL
        HISTORICAL_DATA = load_data(first_candidate['symbol'], first_candidate['timeframe'], first_candidate['start_date'], first_candidate['end_date'])
        BASE_PARAMS = first_candidate['params']
        START_CAPITAL = first_candidate['start_capital']
        
        dummy_study = optuna.create_study()
        start_b = time.time()
        objective(dummy_study.ask())
        end_b = time.time()
        time_per_trial = end_b - start_b
        
        total_trials = n_trials * len(candidates)
        estimated_time = (total_trials * time_per_trial) / n_jobs
        print(f"Geschätzte Gesamtdauer für Stufe 2: {format_time(estimated_time)}")

    best_overall_trial = None
    best_overall_score = -float('inf')
    best_overall_info = {}

    for i, candidate in enumerate(candidates):
        print(f"\n===== Verfeinere Kandidat {i+1}/{len(candidates)} für {candidate['symbol']} ({candidate['timeframe']}) mit {candidate['params']['average_type']} =====")
        
        HISTORICAL_DATA = load_data(candidate['symbol'], candidate['timeframe'], candidate['start_date'], candidate['end_date'])
        BASE_PARAMS = candidate['params']
        START_CAPITAL = candidate['start_capital']
        
        if HISTORICAL_DATA.empty: continue
            
        study = optuna.create_study(direction="maximize")
        study.optimize(objective, n_trials=n_trials, n_jobs=n_jobs, show_progress_bar=True)
        
        if study.best_value > best_overall_score:
            best_overall_score = study.best_value
            best_overall_trial = study.best_trial
            best_overall_info = candidate

    if best_overall_trial:
        print("\n\n" + "="*80)
        print("    +++ FINALES BESTES ERGEBNIS NACH GLOBALER & LOKALER OPTIMIERUNG +++")
        print("="*80)
        
        final_params_dict = best_overall_trial.params
        if 'env_start' in final_params_dict:
            base_envelopes_count = len(best_overall_info['params']['envelopes_pct'])
            env_start = final_params_dict.pop('env_start')
            env_step = final_params_dict.pop('env_step', 0)
            final_params_dict['envelopes_pct'] = [round(env_start + i * env_step, 2) for i in range(base_envelopes_count)]

        final_params = {**BASE_PARAMS, **final_params_dict, 'start_capital': START_CAPITAL, 'max_leverage': 50.0}

        final_data = load_data(best_overall_info['symbol'], best_overall_info['timeframe'], best_overall_info['start_date'], best_overall_info['end_date'])
        data_with_indicators = calculate_envelope_indicators(final_data.copy(), final_params)
        final_result = run_envelope_backtest(data_with_indicators.dropna(), final_params)

        print(f"  HANDELSCOIN: {best_overall_info['symbol']} | TIMEFRAME: {best_overall_info['timeframe']}")
        print(f"  PERFORMANCE-SCORE: {best_overall_score:.2f} (PnL, gewichtet mit Drawdown)")

        trade_log_df = pd.DataFrame(final_result['trade_log'])
        if not trade_log_df.empty:
            min_balance_row = trade_log_df.loc[trade_log_df['balance'].idxmin()]
            print(f"  DATUM MINIMALER KONTOSTAND: {min_balance_row['timestamp']} ({min_balance_row['balance']:.2f} USDT)")

        print("\n  FINALE PERFORMANCE-METRIKEN:")
        print(f"    - Gesamtgewinn (PnL): {final_result['total_pnl_pct']:.2f} %")
        print(f"    - Max. Drawdown:      {final_result['max_drawdown_pct']*100:.2f} %")
        print(f"    - Anzahl Trades:      {final_result['trades_count']}")
        print(f"    - Win-Rate:           {final_result['win_rate']:.2f} %")
        
        trade_log_list = final_result.get('trade_log', [])
        if trade_log_list:
            print("\n  HANDELS-CHRONIK (ERSTE 10 UND LETZTE 10 TRADES):")
            display_limit = 20
            if len(trade_log_list) > display_limit:
                display_list = trade_log_list[:10] + [None] + trade_log_list[-10:]
            else:
                display_list = trade_log_list
            
            print("  " + "-"*106)
            print("  {:^28} | {:<7} | {:<7} | {:>10} | {:>15} | {:>18}".format(
                "Datum & Uhrzeit (UTC)", "Seite", "Hebel", "Stop-Loss", "Gewinn je Trade", "Neuer Kontostand"))
            print("  " + "-"*106)

            for trade in display_list:
                if trade is None:
                    print("  ...".center(108))
                    continue
                side_str = trade['side'].capitalize().ljust(7)
                leverage_str = f"{int(trade.get('leverage', 0))}x".ljust(7)
                sl_price_str = f"{trade.get('stop_loss_price', 0):.4f}".rjust(10)
                pnl_str = f"{trade['pnl']:+9.2f} USDT".rjust(15)
                balance_str = f"{trade['balance']:.2f} USDT".rjust(18)
                print(f"  {trade['timestamp']:<28} | {side_str} | {leverage_str} | {sl_price_str} | {pnl_str} | {balance_str}")
            print("  " + "-"*106)

        print("\n  >>> EINSTELLUNGEN FÜR DEINE 'config.json' <<<")
        config_output = {
            "market": {"symbol": best_overall_info['symbol'], "timeframe": best_overall_info['timeframe']},
            "strategy": {"average_type": final_params['average_type'], "average_period": final_params['average_period'], "envelopes_pct": final_params['envelopes_pct']},
            "risk": {"margin_mode": "isolated", "balance_fraction_pct": 2, "stop_loss_pct": round(final_params['stop_loss_pct'], 2), "base_leverage": final_params['base_leverage'], "max_leverage": int(final_params['max_leverage']), "target_atr_pct": round(final_params['target_atr_pct'], 2)},
            "behavior": {"use_longs": True, "use_shorts": True}
        }
        print(json.dumps(config_output, indent=4))
        print("\n" + "="*80)
    else:
        print("Kein gültiges Ergebnis nach der Verfeinerung gefunden.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stufe 2: Lokale Parameter-Verfeinerung mit Optuna.")
    parser.add_argument('--jobs', type=int, default=1, help='Anzahl der CPU-Kerne für die Optimierung.')
    parser.add_argument('--trials', type=int, default=200, help='Anzahl der Versuche pro Kandidat.')
    args = parser.parse_args()
    main(n_jobs=args.jobs, n_trials=args.trials)
