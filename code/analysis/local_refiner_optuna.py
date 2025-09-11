# code/analysis/local_refiner_optuna.py

import json
import os
import sys
import argparse
import optuna
import numpy as np

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from analysis.backtest import run_smc_backtest
from analysis.global_optimizer_pymoo import load_data, format_time

optuna.logging.set_verbosity(optuna.logging.WARNING)
HISTORICAL_DATA, START_CAPITAL, BASE_PARAMS = None, 1000.0, {}

DB_FILE = "optuna_studies.db"
STORAGE_URL = f"sqlite:///{DB_FILE}"

def objective(trial):
    base_swing = BASE_PARAMS.get('swing_period', 15)
    base_rr = BASE_PARAMS.get('risk_reward_ratio', 2.0)
    base_leverage = BASE_PARAMS.get('leverage', 10)
    base_activation_rr = BASE_PARAMS.get('trailing_stop_activation_rr', 2.0)
    base_callback_rate = BASE_PARAMS.get('trailing_stop_callback_rate_pct', 1.0)

    params = {
        'swing_period': trial.suggest_int('swing_period', max(5, base_swing - 10), base_swing + 10),
        'risk_reward_ratio': trial.suggest_float('risk_reward_ratio', max(1.0, base_rr * 0.8), base_rr * 1.2),
        'leverage': trial.suggest_int('leverage', max(5, base_leverage - 5), base_leverage + 10),
        'trailing_stop_activation_rr': trial.suggest_float('trailing_stop_activation_rr', max(1.0, base_activation_rr * 0.8), base_activation_rr * 1.2),
        'trailing_stop_callback_rate_pct': trial.suggest_float('trailing_stop_callback_rate_pct', max(0.5, base_callback_rate * 0.8), base_callback_rate * 1.2),
        'start_capital': START_CAPITAL, 'risk_per_trade_pct': 1.0
    }
    result = run_smc_backtest(HISTORICAL_DATA.copy(), params)
    pnl = result.get('total_pnl_pct', -1000)
    drawdown = result.get('max_drawdown_pct', 1.0)
    score = pnl * (1 - drawdown**0.5) if pnl > 0 else pnl
    return score if np.isfinite(score) else -float('inf')

def main(n_jobs, n_trials):
    print("\n--- [Stufe 2/2] Lokale Verfeinerung (TitanBot) mit Optuna ---")
    input_file = os.path.join(os.path.dirname(__file__), 'optimization_candidates.json')
    if not os.path.exists(input_file):
        print(f"Fehler: '{input_file}' nicht gefunden. Stufe 1 muss zuerst laufen."); return

    # ... (Der Anfang der main-Funktion bleibt unverändert) ...

    if all_refined_results:
        sorted_results = sorted(all_refined_results, key=lambda x: x['score'], reverse=True)
        
        print("\n\n" + "="*80 + "\n    +++ FINALE TOP-ERGEBNISSE NACH DER OPTIMIERUNG +++\n" + "="*80)
        
        for idx, result in enumerate(sorted_results[:10]):
            info = result['info']
            score = result['score']
            metrics = result['metrics']
            params = result['params']
            
            # ... (Die Ausgabe der Metriken bleibt unverändert) ...
            
            print("  >>> EINSTELLUNGEN FÜR DEINE 'config.json' <<<")
            config_output = {
                "market": {"symbol": info['symbol'], "timeframe": info['timeframe']},
                "strategy": {"swing_period": params['swing_period'], "atr_period": 14},
                "risk": {
                    "margin_mode": "isolated", "balance_fraction_pct": 10, "risk_per_trade_pct": 1.0,
                    "risk_reward_ratio": round(params['risk_reward_ratio'], 2),
                    "leverage": params['leverage'],
                    "trailing_stop_activation_rr": round(params['trailing_stop_activation_rr'], 2),
                    "trailing_stop_callback_rate_pct": round(params['trailing_stop_callback_rate_pct'], 2)
                },
                "behavior": {"use_longs": True, "use_shorts": True}
            }
            print(json.dumps(config_output, indent=4))
            print("-" * 80)
    else:
        print("Kein gültiges Ergebnis nach der Verfeinerung gefunden.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stufe 2: Lokale Parameter-Verfeinerung mit Optuna.")
    parser.add_argument('--jobs', type=int, default=1, help='Anzahl der CPU-Kerne.')
    parser.add_argument('--trials', type=int, default=200, help='Anzahl der Versuche pro Kandidat.')
    args = parser.parse_args()
    main(n_jobs=args.jobs, n_trials=args.trials)
