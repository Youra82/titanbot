# src/titanbot/analysis/portfolio_optimizer.py
# Hybrid: Pre-Filter → Exhaustive Search (≤20 Kandidaten) oder Multi-Start-Greedy (>20)
import itertools
from tqdm import tqdm
import sys
import os
import json

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from titanbot.analysis.portfolio_simulator import run_portfolio_simulation

EXHAUSTIVE_THRESHOLD = 20  # Bis zu dieser Kandidatenzahl: exhaustive, sonst multi-start greedy


def _build_sim_data(files, strategies_data):
    """Baut das sim_data-Dict für run_portfolio_simulation auf. Gibt None zurück bei fehlenden Daten."""
    sim_data = {}
    for fname in files:
        sd = strategies_data.get(fname)
        if not sd or 'data' not in sd or sd['data'].empty:
            return None
        key = f"{sd['symbol']}_{sd['timeframe']}"
        sim_data[key] = sd
    return sim_data


def _no_coin_collision(files, strategies_data):
    """True wenn kein Coin doppelt vorkommt."""
    coins = [strategies_data[f]['symbol'].split('/')[0] for f in files if f in strategies_data]
    return len(coins) == len(set(coins))


def _greedy_from(start_file, candidate_pool, strategies_data, start_capital, start_date, end_date, target_max_dd_decimal):
    """
    Greedy-Lauf startend von start_file.
    Gibt (best_files, best_result) zurück oder (None, None) wenn Startpunkt ungültig.
    """
    sim_data = _build_sim_data([start_file], strategies_data)
    if sim_data is None:
        return None, None

    result = run_portfolio_simulation(start_capital, sim_data, start_date, end_date)
    if not result or result.get("liquidation_date"):
        return None, None
    if result.get('max_drawdown_pct', 100.0) / 100.0 > target_max_dd_decimal:
        return None, None

    best_files = [start_file]
    best_result = result
    best_capital = result['end_capital']
    selected_coins = {strategies_data[start_file]['symbol'].split('/')[0]}
    remaining = [f for f in candidate_pool if f != start_file]

    while True:
        best_addition = None
        best_addition_capital = best_capital
        best_addition_result = best_result

        for candidate in remaining:
            coin = strategies_data.get(candidate, {}).get('symbol', '').split('/')[0]
            if coin in selected_coins:
                continue

            team = best_files + [candidate]
            sim_data = _build_sim_data(team, strategies_data)
            if sim_data is None:
                continue

            res = run_portfolio_simulation(start_capital, sim_data, start_date, end_date)
            if not res or res.get("liquidation_date"):
                continue
            if res.get('max_drawdown_pct', 100.0) / 100.0 > target_max_dd_decimal:
                continue
            if res['end_capital'] > best_addition_capital:
                best_addition_capital = res['end_capital']
                best_addition = candidate
                best_addition_result = res

        if best_addition:
            best_files.append(best_addition)
            selected_coins.add(strategies_data[best_addition]['symbol'].split('/')[0])
            best_capital = best_addition_capital
            best_result = best_addition_result
            remaining.remove(best_addition)
        else:
            break

    return best_files, best_result


def run_portfolio_optimizer(start_capital, strategies_data, start_date, end_date, target_max_dd: float):
    """
    Findet die Kombination von SMC-Strategien mit maximalem Endkapital bei Max DD <= target_max_dd.
    Jeder Coin darf nur einmal vorkommen.

    Algorithmus:
    1. Pre-Filter: Einzelstrategien mit Max DD > Limit oder Liquidation werden verworfen
    2a. ≤ EXHAUSTIVE_THRESHOLD Kandidaten: exhaustive Suche über alle Kombinationen (global optimal)
    2b. > EXHAUSTIVE_THRESHOLD Kandidaten: Multi-Start-Greedy (jede Einzelstrategie als Startpunkt)
    """
    print(f"\n--- Portfolio-Optimierung: Max DD <= {target_max_dd:.2f}% ---")
    target_max_dd_decimal = target_max_dd / 100.0

    if not strategies_data:
        print("Keine Strategien gefunden.")
        return None

    # --- 1. Pre-Filter: Einzelstrategien bewerten ---
    print(f"1/2: Pre-Filter — {len(strategies_data)} Configs werden einzeln getestet...")
    valid_candidates = []

    for filename, strat_data in tqdm(strategies_data.items(), desc="Einzeltest"):
        key = f"{strat_data['symbol']}_{strat_data['timeframe']}"
        sim_data = {key: strat_data}
        if 'data' not in strat_data or strat_data['data'].empty:
            continue

        result = run_portfolio_simulation(start_capital, sim_data, start_date, end_date)
        if not result or result.get("liquidation_date"):
            continue

        actual_dd = result.get('max_drawdown_pct', 100.0) / 100.0
        if actual_dd <= target_max_dd_decimal:
            valid_candidates.append({
                'filename': filename,
                'end_capital': result['end_capital'],
                'max_dd': actual_dd * 100,
                'result': result,
            })

    if not valid_candidates:
        print(f"Keine Einzelstrategie erfüllte Max DD <= {target_max_dd:.2f}%.")
        return {"optimal_portfolio": [], "final_result": None}

    # Sortiere nach Endkapital (bestes zuerst)
    valid_candidates.sort(key=lambda x: x['end_capital'], reverse=True)
    candidate_files = [c['filename'] for c in valid_candidates]
    n = len(candidate_files)
    print(f"-> {n} Kandidaten nach Pre-Filter übrig.")

    # --- 2. Suche ---
    best_files = None
    best_result = None
    best_capital = -1

    if n <= EXHAUSTIVE_THRESHOLD:
        # --- 2a. Exhaustive Search ---
        total_combos = 2**n - 1
        print(f"2/2: Exhaustive Search über {total_combos} Kombinationen ({n} Kandidaten)...")

        for size in range(1, n + 1):
            for combo in tqdm(list(itertools.combinations(candidate_files, size)),
                              desc=f"Größe {size}/{n}", leave=False):
                if not _no_coin_collision(list(combo), strategies_data):
                    continue
                sim_data = _build_sim_data(list(combo), strategies_data)
                if sim_data is None:
                    continue
                res = run_portfolio_simulation(start_capital, sim_data, start_date, end_date)
                if not res or res.get("liquidation_date"):
                    continue
                if res.get('max_drawdown_pct', 100.0) / 100.0 > target_max_dd_decimal:
                    continue
                if res['end_capital'] > best_capital:
                    best_capital = res['end_capital']
                    best_files = list(combo)
                    best_result = res

    else:
        # --- 2b. Multi-Start-Greedy ---
        print(f"2/2: Multi-Start-Greedy — {n} Startpunkte werden probiert...")
        for i, start_file in enumerate(candidate_files):
            print(f"  Startpunkt {i+1}/{n}: {start_file}")
            files, result = _greedy_from(
                start_file, candidate_files, strategies_data,
                start_capital, start_date, end_date, target_max_dd_decimal
            )
            if result and result['end_capital'] > best_capital:
                best_capital = result['end_capital']
                best_files = files
                best_result = result
                print(f"    -> Neues Optimum: {best_capital:.2f} USDT, DD: {best_result['max_drawdown_pct']:.2f}%")

    if best_files is None:
        print(f"Kein Portfolio gefunden das Max DD <= {target_max_dd:.2f}% einhält.")
        return {"optimal_portfolio": [], "final_result": None}

    print(f"\nOptimum: {len(best_files)} Strategien | Endkapital: {best_capital:.2f} USDT | Max DD: {best_result['max_drawdown_pct']:.2f}%")

    # Speichern
    try:
        results_dir = os.path.join(PROJECT_ROOT, 'artifacts', 'results')
        os.makedirs(results_dir, exist_ok=True)
        output_path = os.path.join(results_dir, 'optimization_results.json')
        with open(output_path, 'w') as f:
            json.dump({"optimal_portfolio": best_files}, f, indent=4)
        print(f"Gespeichert: '{output_path}'")
    except Exception as e:
        print(f"Fehler beim Speichern: {e}")

    return {"optimal_portfolio": best_files, "final_result": best_result}
