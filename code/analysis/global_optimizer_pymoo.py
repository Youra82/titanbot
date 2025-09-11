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
                'swing_period': swing_period, 'risk_reward_ratio': round(ind[1], 2), 'leverage': int(round(ind[2])),
                'trailing_stop_activation_rr': round(ind[3], 2), 'trailing_stop_callback_rate_pct': round(ind[4], 2),
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
        try:
            with open(PIPELINE_STATE_FILE, 'r') as f: state = json.load(f)
            all_champions, completed_tasks = state.get('champions', []), state.get('completed_tasks', [])
            print(f"{len(completed_tasks)} Aufgabe(n) bereits abgeschlossen.")
            if os.path.exists(PYMOO_CHECKPOINT_FILE):
                 with open(PYMOO_CHECKPOINT_FILE, 'rb') as f: algorithm = pickle.load(f)
            with open(INPUTS_FILE, 'r') as f: inputs = json.load(f)
            symbol_input, timeframe_input, start_date, end_date = inputs['symbol'], inputs['timeframe'], inputs['start_date'], inputs['end_date']
            n_gen, START_CAPITAL, MINIMUM_TRADES = inputs['n_gen'], inputs['start_capital'], inputs['minimum_trades']
            leverage_min, leverage_max = inputs['leverage_min'], inputs['leverage_max']
        except Exception as e:
            print(f"Fehler beim Laden des Checkpoints ({e}). Starte eine neue Optimierung.")
            resume = False; algorithm = None
            if os.path.exists(PYMOO_CHECKPOINT_FILE): os.remove(PYMOO_CHECKPOINT_FILE)
            if os.path.exists(PIPELINE_STATE_FILE): os.remove(PIPELINE_STATE_FILE)
            if os.path.exists(INPUTS_FILE): os.remove(INPUTS_FILE)

    if not resume:
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

    tasks = [f"{s.upper()}_{tf}" for s in symbol_input.split() for tf in timeframe_input.split()]
    pop_size = 100
    
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
            print("\nFühre realistischen Benchmark durch (simuliert eine ganze Generation)...")
            problem_for_benchmark = SMCOptimizationProblem(leverage_min=leverage_min, leverage_max=leverage_max)
            sample_individuals = np.random.rand(pop_size, 5) * (problem_for_benchmark.xu - problem_for_benchmark.xl) + problem_for_benchmark.xl
            start_b = time.time()
            problem_for_benchmark._evaluate(sample_individuals, out={})
            end_b = time.time()
            time_per_generation = end_b - start_b
            estimated_time = (time_per_generation * n_gen) / n_procs
            print(f"Geschätzte Gesamtdauer für Stufe 1: {format_time(estimated_time)}")
            
            ref_dirs = get_reference_directions("das-dennis", 2, n_partitions=99)
            algorithm = NSGA3(pop_size=pop_size, ref_dirs=ref_dirs)

        with Pool(n_procs) as pool:
            problem = SMCOptimizationProblem(leverage_min=leverage_min, leverage_max=leverage_max, parallelization=StarmapParallelization(pool.starmap))
            termination = get_termination("n_gen", n_gen)
            initial_gen = algorithm.n_gen if algorithm.n_gen else 0
            with tqdm(total=n_gen, initial=initial_gen, desc=f"Optimiere {symbol_full} ({tf})") as pbar:
                if initial_gen > 0: pbar.update(0)
                
                # --- FINALE KORREKTUR ---
                algorithm.setup(problem, seed=1, verbose=False)
                algorithm.termination = termination
                algorithm.callback = CombinedCallback(pbar, every_n_gen_checkpoint=5)

                while algorithm.has_next():
                    algorithm.next()
                
                res = algorithm.result()
                # --- ENDE DER KORREKTUR ---

        valid_indices = [i for i, f in enumerate(res.F) if f[0] < -1]
        if valid_indices:
            best_indices = sorted(valid_indices, key=lambda i: res.F[i][0])[:5]
            for i in best_indices:
                p = res.X[i]
                all_champions.append({
                    'symbol': symbol_full, 'timeframe': tf, 'start_date': start_date, 'end_date': end_date,
                    'start_capital': START_CAPITAL, 'pnl': -res.F[i][0], 'drawdown': res.F[i][1],
                    'params': {
                        'swing_period': int(round(p[0])), 'risk_reward_ratio': round(p[1], 2), 'leverage': int(round(p[2])),
                        'trailing_stop_activation_rr': round(p[3], 2), 'trailing_stop_callback_rate_pct': round(p[4], 2)
                    }
                })
        
        completed_tasks.append(task)
        with open(PIPELINE_STATE_FILE, 'w') as f:
            json.dump({'completed_tasks': completed_tasks, 'champions': all_champions}, f)
        
        if os.path.exists(PYMOO_CHECKPOINT_FILE):
            os.remove(PYMOO_CHECKPOINT_FILE)
        
        algorithm = None

    if not all_champions:
        print("\nKeine vielversprechenden Kandidaten gefunden.")
        sys.exit(1)

    output_file = os.path.join(os.path.dirname(__file__), 'optimization_candidates.json')
    with open(output_file, 'w') as f:
        json.dump(all_champions, f, indent=4)
    print(f"\n--- Globale Suche beendet. Top-Kandidaten in '{output_file}' gespeichert. ---")
    sys.exit(0)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stufe 1: Globale Parameter-Optimierung mit Pymoo.")
    parser.add_argument('--jobs', type=int, default=1, help='Anzahl der CPU-Kerne.')
    parser.add_argument('--gen', type=int, default=50, help='Standard-Anzahl der Generationen.')
    parser.add_argument('--resume', action='store_true', help='Setze eine unterbrochene Optimierung fort.')
    args = parser.parse_args()
    main(n_procs=args.jobs, n_gen_default=args.gen, resume=args.resume)
