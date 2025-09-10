# code/analysis/global_optimizer_pymoo.py

import json
import time
import numpy as np
import os
import sys # <-- Wichtig für sys.exit()
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

# ... (Alle Klassen und die format_time Funktion bleiben exakt wie zuvor) ...
class CombinedCallback(Callback):
    def __init__(self, pbar, every_n_gen_checkpoint=5):
        super().__init__()
        self.pbar = pbar
        self.every_n_gen_checkpoint = every_n_gen_checkpoint

    def notify(self, algorithm):
        self.pbar.update(1)
        if algorithm.n_gen > 0 and algorithm.n_gen % self.every_n_gen_checkpoint == 0:
            tmp_checkpoint_file = CHECKPOINT_FILE + ".tmp"
            callback_ref = algorithm.callback
            algorithm.callback = None
            try:
                with open(tmp_checkpoint_file, 'wb') as f:
                    pickle.dump(algorithm, f)
                os.rename(tmp_checkpoint_file, CHECKPOINT_FILE)
            finally:
                algorithm.callback = callback_ref

class SMCOptimizationProblem(Problem):
    # ... (Diese Klasse bleibt unverändert)
    pass

def main(n_procs, n_gen_default, resume):
    # ... (Der Anfang der main-Funktion mit den Abfragen bleibt unverändert) ...

    # --- KORREKTUR AM ENDE DER FUNKTION ---
    if not all_champions:
        print("\nKeine vielversprechenden Kandidaten gefunden.")
        sys.exit(1) # Beende mit Fehlercode 1

    output_file = os.path.join(os.path.dirname(__file__), 'optimization_candidates.json')
    with open(output_file, 'w') as f:
        json.dump(all_champions, f, indent=4)
    print(f"\n--- Globale Suche beendet. Top-Kandidaten in '{output_file}' gespeichert. ---")
    sys.exit(0) # Beende mit Erfolgscode 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stufe 1: Globale Parameter-Optimierung mit Pymoo.")
    parser.add_argument('--jobs', type=int, default=1, help='Anzahl der CPU-Kerne.')
    parser.add_argument('--gen', type=int, default=50, help='Standard-Anzahl der Generationen.')
    parser.add_argument('--resume', action='store_true', help='Setze eine unterbrochene Optimierung fort.')
    args = parser.parse_args()
    main(n_procs=args.jobs, n_gen_default=args.gen, resume=args.resume)
