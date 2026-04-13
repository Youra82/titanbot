# src/titanbot/analysis/portfolio_optimizer.py (Exhaustive Search – alle Kombinationen werden geprüft)
import itertools
from tqdm import tqdm
import sys
import os
import json

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from titanbot.analysis.portfolio_simulator import run_portfolio_simulation


def run_portfolio_optimizer(start_capital, strategies_data, start_date, end_date, target_max_dd: float):
    """
    Findet die Kombination von SMC-Strategien, die das höchste Endkapital liefert,
    während der maximale Drawdown unter target_max_dd bleibt UND jeder Coin nur einmal vorkommt.

    Verwendet exhaustive Suche über alle Kombinationen (2^n, praktisch bis ~20 Strategien).
    Dadurch wird garantiert das globale Optimum gefunden – kein Greedy-Bias.
    """
    print(f"\n--- Starte automatische Portfolio-Optimierung (Exhaustive Search) mit Max DD <= {target_max_dd:.2f}% ---")
    target_max_dd_decimal = target_max_dd / 100.0

    if not strategies_data:
        print("Keine Strategien zum Optimieren gefunden.")
        return None

    all_files = list(strategies_data.keys())
    n = len(all_files)

    # Alle Kombinationen von Größe 1 bis n generieren
    all_combinations = []
    for size in range(1, n + 1):
        for combo in itertools.combinations(all_files, size):
            all_combinations.append(combo)

    total = len(all_combinations)
    print(f"Prüfe {total} Kombinationen ({n} Strategien, 2^{n}-1 möglich)...")

    best_end_capital = -1
    best_combo = None
    best_result = None
    skipped_collision = 0
    skipped_dd = 0
    skipped_liq = 0

    for combo in tqdm(all_combinations, desc="Durchsuche Kombinationen"):
        # 1. Coin-Kollisionsprüfung (jeder Coin nur einmal)
        coins_in_combo = [strategies_data[f]['symbol'].split('/')[0] for f in combo]
        if len(coins_in_combo) != len(set(coins_in_combo)):
            skipped_collision += 1
            continue

        # 2. Simulationsdaten zusammenstellen
        sim_data = {}
        valid = True
        for fname in combo:
            sd = strategies_data.get(fname)
            if not sd or 'data' not in sd or sd['data'].empty:
                valid = False
                break
            key = f"{sd['symbol']}_{sd['timeframe']}"
            sim_data[key] = sd
        if not valid:
            continue

        # 3. Simulation ausführen
        result = run_portfolio_simulation(start_capital, sim_data, start_date, end_date)

        if not result or result.get("liquidation_date"):
            skipped_liq += 1
            continue

        actual_max_dd = result.get('max_drawdown_pct', 100.0) / 100.0

        if actual_max_dd > target_max_dd_decimal:
            skipped_dd += 1
            continue

        # 4. Bestes Ergebnis merken
        if result['end_capital'] > best_end_capital:
            best_end_capital = result['end_capital']
            best_combo = list(combo)
            best_result = result

    print(f"\nSuche abgeschlossen.")
    print(f"  Übersprungen wegen Coin-Kollision: {skipped_collision}")
    print(f"  Übersprungen wegen Max DD > {target_max_dd:.1f}%: {skipped_dd}")
    print(f"  Übersprungen wegen Liquidation: {skipped_liq}")

    if best_combo is None:
        print(f"Kein Portfolio gefunden, das Max DD <= {target_max_dd:.2f}% einhält.")
        return {"optimal_portfolio": [], "final_result": None}

    print(f"Globales Optimum: {len(best_combo)} Strategien, Endkapital: {best_end_capital:.2f} USDT, Max DD: {best_result['max_drawdown_pct']:.2f}%")

    # Ergebnisse speichern
    try:
        results_dir = os.path.join(PROJECT_ROOT, 'artifacts', 'results')
        os.makedirs(results_dir, exist_ok=True)
        output_path = os.path.join(results_dir, 'optimization_results.json')
        with open(output_path, 'w') as f:
            json.dump({"optimal_portfolio": best_combo}, f, indent=4)
        print(f"Optimales Portfolio gespeichert in '{output_path}'.")
    except Exception as e:
        print(f"Fehler beim Speichern: {e}")

    return {"optimal_portfolio": best_combo, "final_result": best_result}
