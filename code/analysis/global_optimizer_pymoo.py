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
CHECKPOINT_FILE = "pymoo_checkpoint.pkl"
INPUTS_FILE = "optim_inputs.json"

class TqdmCallback(Callback):
    def __init__(self, pbar):
        super().__init__()
        self.pbar = pbar
    def notify(self, algorithm):
        self.pbar.update(1)

class CheckpointCallback(Callback):
    def __init__(self, every_n_gen=5):
        super().__init__()
        self.every_n_gen = every_n_gen

    def notify(self, algorithm):
        if algorithm.n_gen > 0 and algorithm.n_gen % self.every_n_gen == 0:
            with open(CHECKPOINT_FILE, 'wb') as f:
                pickle.dump(algorithm, f)

def format_time(seconds):
    if seconds < 60: return f"{seconds:.1f} Sekunden"
    minutes = int(seconds // 60); remaining_seconds = int(seconds % 60)
    if minutes < 60: return f"{minutes} Minuten und {remaining_seconds} Sekunden"
    hours = int(minutes // 60); remaining_minutes = int(minutes % 60)
    return f"{hours} Stunden, {remaining_minutes} Minuten und {remaining_seconds} Sekunden"

class SMCOptimizationProblem(Problem):
    def __init__(self, leverage_min=5, leverage_max=50, **kwargs):
        lower_bounds = [5, 1.0, leverage_min]
        upper_bounds = [50, 5.0, leverage_max]
        super().__init__(n_var=3, n_obj=2, n_constr=0, xl=lower_bounds, xu=upper_bounds, **kwargs)

    def _evaluate(self, x, out, *args, **kwargs):
        results = []
        for ind in x:
            swing_period = int(round(ind[0]))
            if swing_period in INDICATOR_CACHE:
                data_with_indicators = INDICATOR_CACHE[swing_period]
            else:
                temp_params = {'swing_period': swing_period, 'atr_period': 14}
                data_with_indicators = calculate_smc_indicators(HISTORICAL_DATA.copy(), temp_params)
                INDICATOR_CACHE[swing_period] = data_with_indicators
            
            params = {
                'swing_period': swing_period, 'risk_reward_ratio': round(ind[1], 2), 'leverage': int(round(ind[2])),
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
    
    algorithm = None
    if resume and os.path.exists(CHECKPOINT_FILE):
        print("\nLade gespeicherten Fortschritt aus Checkpoint-Datei...")
        with open(CHECKPOINT_FILE, 'rb') as f: algorithm = pickle.load(f)
        with open(INPUTS_FILE, 'r') as f: inputs = json.load(f)
        symbol_input, timeframe_input, start_date, end_date = inputs['symbol'], inputs['timeframe'], inputs['start_date'], inputs['end_date']
        n_gen, START_CAPITAL, MINIMUM_TRADES = inputs['n_gen'], inputs['start_capital'], inputs['minimum_trades']
        leverage_min, leverage_max = inputs['leverage_min'], inputs['leverage_max']
        print("Optimierung wird fortgesetzt.")
    else:
        symbol_input = input("Handelspaar(e) eingeben (z.B. BTC ETH): ")
        timeframe_input = input("Zeitfenster eingeben (z.B. 1h 4h): ")
        start_date = input("Startdatum eingeben (JJJJ-MM-TT): ")
        end_date = input("Enddatum eingeben (JJJJ-MM-TT): ")
        n_gen = int(input(f"Anzahl der Generationen (Standard: {n_gen_default}): ") or n_gen_default)
        START_CAPITAL = float(input("Startkapital in USDT (z.B. 1000): "))
        MINIMUM_TRADES = int(input("Mindestanzahl an Trades (z.B. 20): "))
        leverage_min = int(input("Minimaler Hebel für die Suche (z.B. 5): "))
        leverage_max = int(input("Maximaler Hebel für die Suche (z.B. 50): "))
        inputs_to_save = {'symbol': symbol_input, 'timeframe': timeframe_input, 'start_date': start_date, 'end_date': end_date,
                          'n_gen': n_gen, 'start_capital': START_CAPITAL, 'minimum_trades': MINIMUM_TRADES,
                          'leverage_min': leverage_min, 'leverage_max': leverage_max}
        with open(INPUTS_FILE, 'w') as f: json.dump(inputs_to_save, f)

    all_champions = []
    for symbol_short in symbol_input.split():
        for tf in timeframe_input.split():
            symbol_full = f"{symbol_short.upper()}/USDT:USDT"
            global INDICATOR_CACHE, HISTORICAL_DATA
            INDICATOR_CACHE = {}
            HISTORICAL_DATA = load_data(symbol_full, tf, start_date, end_date)
            if HISTORICAL_DATA.empty: continue
            
            pop_size = 100
            
            if algorithm is None:
                # --- START: VERBESSERTER BENCHMARK ---
                print("\nFühre verbesserten Benchmark zur Zeitschätzung durch (10 Testläufe)...")
                benchmark_runs = 10
                problem_for_benchmark = SMCOptimizationProblem(leverage_min=leverage_min, leverage_max=leverage_max)
                
                # Erstelle 10 zufällige Individuen zum Testen
                sample_individuals = np.random.rand(benchmark_runs, 3) * (problem_for_benchmark.xu - problem_for_benchmark.xl) + problem_for_benchmark.xl
                
                start_b = time.time()
                for i in range(benchmark_runs):
                    problem_for_benchmark._evaluate(sample_individuals[i:i+1], out={})
                end_b = time.time()
                
                total_benchmark_time = end_b - start_b
                avg_time_per_eval = total_benchmark_time / benchmark_runs

                total_evals = pop_size * n_gen
                estimated_time = (total_evals * avg_time_per_eval) / n_procs
                print(f"Geschätzte Gesamtdauer für Stufe 1: {format_time(estimated_time)}")
                # --- ENDE: VERBESSERTER BENCHMARK ---
                
                ref_dirs = get_reference_directions("das-dennis", 2, n_partitions=99)
                algorithm = NSGA3(pop_size=pop_size, ref_dirs=ref_dirs)

            with Pool(n_procs) as pool:
                problem = SMCOptimizationProblem(leverage_min=leverage_min, leverage_max=leverage_max, parallelization=StarmapParallelization(pool.starmap))
                termination = get_termination("n_gen", n_gen)
                initial_gen = algorithm.n_gen if algorithm.n_gen else 0
                with tqdm(total=n_gen, initial=initial_gen, desc=f"Optimiere {symbol_full} ({tf})") as pbar:
                    if initial_gen > 0: pbar.update(0)
                    callbacks = [TqdmCallback(pbar), CheckpointCallback(every_n_gen=5)]
                    res = minimize(problem, algorithm, termination, seed=1, callback=callbacks, verbose=False)

            valid_indices = [i for i, f in enumerate(res.F) if f[0] < -1]
            if not valid_indices: continue
            best_indices = sorted(valid_indices, key=lambda i: res.F[i][0])[:5]
            for i in best_indices:
                p = res.X[i]
                all_champions.append({
                    'symbol': symbol_full, 'timeframe': tf, 'start_date': start_date, 'end_date': end_date,
                    'start_capital': START_CAPITAL, 'pnl': -res.F[i][0], 'drawdown': res.F[i][1],
                    'params': {'swing_period': int(round(p[0])), 'risk_reward_ratio': round(p[1], 2), 'leverage': int(round(p[2]))}
                })
            algorithm = None

    if not all_champions: print("\nKeine vielversprechenden Kandidaten gefunden."); return
    output_file = os.path.join(os.path.dirname(__file__), 'optimization_candidates.json')
    with open(output_file, 'w') as f: json.dump(all_champions, f, indent=4)
    print(f"\n--- Globale Suche beendet. Top-Kandidaten in '{output_file}' gespeichert. ---")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stufe 1: Globale Parameter-Optimierung mit Pymoo.")
    parser.add_argument('--jobs', type=int, default=1, help='Anzahl der CPU-Kerne.')
    parser.add_argument('--gen', type=int, default=50, help='Standard-Anzahl der Generationen.')
    parser.add_argument('--resume', action='store_true', help='Setze eine unterbrochene Optimierung fort.')
    args = parser.parse_args()
    main(n_procs=args.jobs, n_gen_default=args.gen, resume=args.resume)
