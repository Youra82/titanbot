# code/analysis/global_optimizer_pymoo.py

import json
import time
import numpy as np
import os
import sys
import argparse
import pickle
from multiprocessing import Pool
from tqdm import tqdm

from pymoo.core.problem import StarmapParallelization, Problem
from pymoo.algorithms.moo.nsga3 import NSGA3
from pymoo.util.ref_dirs import get_reference_directions
from pymoo.optimize import minimize
from pymoo.termination import get_termination
from pymoo.core.callback import Callback

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from analysis.backtest import load_data, run_smc_backtest
from utilities.strategy_logic import calculate_smc_indicators

HISTORICAL_DATA, START_CAPITAL, MINIMUM_TRADES = None, 1000.0, 10
INDICATOR_CACHE = {}
PYMOO_CHECKPOINT_FILE = "pymoo_checkpoint.pkl"
PIPELINE_STATE_FILE = "pipeline_state.json"
INPUTS_FILE = "optim_inputs.json"

class CombinedCallback(Callback):
    def __init__(self, pbar, every_n_gen_checkpoint=5):
        super().__init__()
        self.pbar = pbar
        self.every_n_gen_checkpoint = every_n_gen_checkpoint

    def notify(self, algorithm):
        self.pbar.update(1)
        if algorithm.n_gen > 0 and algorithm.n_gen % self.every_n_gen_checkpoint == 0:
            tmp_checkpoint_file = PYMOO_CHECKPOINT_FILE + ".tmp"
            callback_ref = algorithm.callback
            algorithm.callback = None
            try:
                with open(tmp_checkpoint_file, 'wb') as f:
                    pickle.dump(algorithm, f)
                os.rename(tmp_checkpoint_file, PYMOO_CHECKPOINT_FILE)
            finally:
                algorithm.callback = callback_ref

def format_time(seconds):
    if seconds < 60: return f"{seconds:.1f} Sekunden"
    minutes = int(seconds // 60); remaining_seconds = int(seconds % 60)
    if minutes < 60: return f"{minutes} Minuten und {remaining_seconds} Sekunden"
    hours = int(minutes // 60); remaining_minutes = int(minutes % 60)
    return f"{hours} Stunden, {remaining_minutes} Minuten und {remaining_seconds} Sekunden"

class SMCOptimizationProblem(Problem):
    def __init__(self, leverage_min=5, leverage_max=50, **kwargs):
        lower_bounds = [5, 1.0, leverage_min, 1.0, 0.5]
        upper_bounds = [50, 5.0, leverage_max, 5.0, 5.0]
        super().__init__(n_var=5, n_obj=2, n_constr=0, xl=lower_bounds, xu=upper_bounds, **kwargs)

    def _evaluate(self, x, out, *args, **kwargs):
        results = []
        for ind in x:
            swing_period = int(round(ind[0]))
            if swing_period in INDICATOR_CACHE: data_with_indicators = INDICATOR_CACHE[swing_period]
            else:
                temp_params = {'swing_period': swing_period, 'atr_period': 14}
                data_with_indicators = calculate_smc_indicators(HISTORICAL_DATA.copy(), temp_params)
                INDICATOR_CACHE[swing_period] = data_with_indicators
            
            params = {
                'swing_period': swing_period,
                'risk_reward_ratio': round(ind[1], 2),
                'leverage': int(round(ind[2])),
                'trailing_stop_activation_rr': round(ind[3], 2),
                'trailing_stop_callback_rate_pct': round(ind[4], 2),
                'start_capital': START_CAPITAL, 'risk_per_trade_pct': 1.0
            }
            result = run_smc_backtest(data_with_indicators.copy(), params)
            pnl = result.get('total_pnl_pct', -1000)
            drawdown = result.get('max_drawdown_pct', 1.0) * 100
            if result['trades_count'] < MINIMUM_TRADES: pnl = -1000
            results.append([-pnl, drawdown])
        out["F"] = np.array(results)

def main(n_procs, n_gen_default, resume):
    print("\n--- [Stufe 1/2] Globale Suche (TitanBot) mit Pymoo ---")
    
    algorithm, all_champions, completed_tasks = None, [], []
    
    if resume and os.path.exists(PIPELINE_STATE_FILE):
        print("\nLade gespeicherten Fortschritt der Pipeline...")
        # ... (Logik zum Fortsetzen bleibt unverändert) ...
    else:
        # ... (Logik für neuen Start bleibt unverändert) ...

    tasks = [f"{s.upper()}_{tf}" for s in symbol_input.split() for tf in timeframe_input.split()]
    
    for task in tasks:
        symbol_short, tf = task.split('_')
        symbol_full = f"{symbol_short}/USDT:USDT"
        
        if task in completed_tasks:
            print(f"\nÜberspringe bereits abgeschlossene Aufgabe: {symbol_full} ({tf})")
            continue
            
        global INDICATOR_CACHE, HISTORICAL_DATA
        INDICATOR_CACHE = {}
        HISTORICAL_DATA = load_data(symbol_full, tf, start_date, end_date)
        if HISTORICAL_DATA.empty: continue
        
        if algorithm is None:
            print("\nFühre verbesserten Benchmark zur Zeitschätzung durch (10 Testläufe)...")
            pop_size = 100
            benchmark_runs = 10
            problem_for_benchmark = SMCOptimizationProblem(leverage_min=leverage_min, leverage_max=leverage_max)
            sample_individuals = np.random.rand(benchmark_runs, 5) * (problem_for_benchmark.xu - problem_for_benchmark.xl) + problem_for_benchmark.xl
            # ... (Rest der Benchmark-Logik bleibt unverändert) ...
            
            ref_dirs = get_reference_directions("das-dennis", 2, n_partitions=99)
            algorithm = NSGA3(pop_size=pop_size, ref_dirs=ref_dirs)

        with Pool(n_procs) as pool:
            problem = SMCOptimizationProblem(leverage_min=leverage_min, leverage_max=leverage_max, parallelization=StarmapParallelization(pool.starmap))
            # ... (Rest der Optimierungs-Logik mit dem direkten Aufruf bleibt unverändert) ...

        if valid_indices:
            best_indices = sorted(valid_indices, key=lambda i: res.F[i][0])[:5]
            for i in best_indices:
                p = res.X[i]
                all_champions.append({
                    'symbol': symbol_full, 'timeframe': tf, 'start_date': start_date, 'end_date': end_date,
                    'start_capital': START_CAPITAL, 'pnl': -res.F[i][0], 'drawdown': res.F[i][1],
                    'params': {
                        'swing_period': int(round(p[0])),
                        'risk_reward_ratio': round(p[1], 2),
                        'leverage': int(round(p[2])),
                        'trailing_stop_activation_rr': round(p[3], 2),
                        'trailing_stop_callback_rate_pct': round(p[4], 2)
                    }
                })
        
        # ... (Rest der main-Funktion bleibt unverändert) ...
