# src/titanbot/analysis/portfolio_optimizer.py
# Hybrid: Pre-Filter → Exhaustive Search (≤20 Kandidaten) oder Multi-Start-Greedy (>20)
import itertools
import contextlib
import io
import sys
import os
import json

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from titanbot.analysis.portfolio_simulator import run_portfolio_simulation

EXHAUSTIVE_THRESHOLD = 20  # Bis zu dieser Kandidatenzahl: exhaustive, sonst multi-start greedy
MAX_GREEDY_STARTS = 10    # Multi-Start-Greedy: nur die Top-N Einzelstrategien als Startpunkt


def _simulate_silent(start_capital, sim_data, start_date, end_date):
    """Führt Simulation aus und unterdrückt deren gesamten Print-Output."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return run_portfolio_simulation(start_capital, sim_data, start_date, end_date)


def _build_sim_data(files, strategies_data):
    sim_data = {}
    for fname in files:
        sd = strategies_data.get(fname)
        if not sd or 'data' not in sd or sd['data'].empty:
            return None
        key = f"{sd['symbol']}_{sd['timeframe']}"
        sim_data[key] = sd
    return sim_data


def _no_coin_collision(files, strategies_data):
    coins = [strategies_data[f]['symbol'].split('/')[0] for f in files if f in strategies_data]
    return len(coins) == len(set(coins))


def _greedy_from(start_file, candidate_pool, strategies_data, start_capital, start_date, end_date,
                 target_max_dd_decimal, sim_counter):
    """
    Greedy-Lauf von start_file aus. Gibt (best_files, best_result, sim_counter) zurück.
    sim_counter ist ein dict {'done': int, 'total': int} für Fortschrittsanzeige.
    """
    sim_data = _build_sim_data([start_file], strategies_data)
    if sim_data is None:
        return None, None, sim_counter

    sim_counter['done'] += 1
    result = _simulate_silent(start_capital, sim_data, start_date, end_date)
    if not result or result.get("liquidation_date"):
        return None, None, sim_counter
    if result.get('max_drawdown_pct', 100.0) / 100.0 > target_max_dd_decimal:
        return None, None, sim_counter

    best_files = [start_file]
    best_result = result
    best_capital = result['end_capital']
    selected_coins = {strategies_data[start_file]['symbol'].split('/')[0]}
    remaining = [f for f in candidate_pool if f != start_file]

    step = 0
    while True:
        step += 1
        best_addition = None
        best_addition_capital = best_capital
        best_addition_result = best_result

        candidates_this_round = [f for f in remaining
                                  if strategies_data.get(f, {}).get('symbol', '').split('/')[0] not in selected_coins]

        for idx, candidate in enumerate(candidates_this_round):
            team = best_files + [candidate]
            sim_data = _build_sim_data(team, strategies_data)
            if sim_data is None:
                continue

            sim_counter['done'] += 1
            print(f"\r  Sim {sim_counter['done']}/{sim_counter['total']} | "
                  f"Start {sim_counter['start_i']}/{sim_counter['start_n']} | "
                  f"Schritt {step} | Kandidat {idx+1}/{len(candidates_this_round)} | "
                  f"Bestes Kapital: {sim_counter['best_capital']:.2f} USDT",
                  end='', flush=True)

            res = _simulate_silent(start_capital, sim_data, start_date, end_date)
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
            if best_capital > sim_counter['best_capital']:
                sim_counter['best_capital'] = best_capital
        else:
            break

    return best_files, best_result, sim_counter


def run_portfolio_optimizer(start_capital, strategies_data, start_date, end_date, target_max_dd: float):
    print(f"\n--- Portfolio-Optimierung: Max DD <= {target_max_dd:.2f}% ---")
    target_max_dd_decimal = target_max_dd / 100.0

    if not strategies_data:
        print("Keine Strategien gefunden.")
        return None

    # --- 1. Pre-Filter ---
    total = len(strategies_data)
    print(f"1/2: Pre-Filter — {total} Configs werden einzeln getestet...")
    valid_candidates = []

    for i, (filename, strat_data) in enumerate(strategies_data.items(), 1):
        print(f"\r  [{i:>3}/{total}] {filename:<50}", end='', flush=True)
        key = f"{strat_data['symbol']}_{strat_data['timeframe']}"
        sim_data = {key: strat_data}
        if 'data' not in strat_data or strat_data['data'].empty:
            continue

        result = _simulate_silent(start_capital, sim_data, start_date, end_date)
        if not result or result.get("liquidation_date"):
            continue

        actual_dd = result.get('max_drawdown_pct', 100.0) / 100.0
        if actual_dd <= target_max_dd_decimal and result['end_capital'] > start_capital:
            valid_candidates.append({
                'filename': filename,
                'end_capital': result['end_capital'],
                'max_dd': actual_dd * 100,
                'result': result,
            })

    print()  # Zeilenumbruch nach letztem \r
    if not valid_candidates:
        print(f"Keine Einzelstrategie erfüllte Max DD <= {target_max_dd:.2f}%.")
        return {"optimal_portfolio": [], "final_result": None}

    valid_candidates.sort(key=lambda x: x['end_capital'], reverse=True)
    candidate_files = [c['filename'] for c in valid_candidates]
    n = len(candidate_files)
    print(f"-> {n}/{total} Kandidaten bestehen Pre-Filter:")
    for c in valid_candidates:
        print(f"   {c['filename']:<50} | Kapital: {c['end_capital']:>8.2f} USDT | DD: {c['max_dd']:>5.1f}%")

    # --- 2. Suche ---
    best_files = None
    best_result = None
    best_capital = -1

    if n <= EXHAUSTIVE_THRESHOLD:
        total_combos = 2**n - 1
        print(f"\n2/2: Exhaustive Search — {total_combos} Kombinationen ({n} Kandidaten)...")
        done = 0
        for size in range(1, n + 1):
            combos = list(itertools.combinations(candidate_files, size))
            for combo in combos:
                done += 1
                print(f"\r  [{done:>6}/{total_combos}] Größe {size} | Bestes Kapital: {best_capital:.2f} USDT",
                      end='', flush=True)
                if not _no_coin_collision(list(combo), strategies_data):
                    continue
                sim_data = _build_sim_data(list(combo), strategies_data)
                if sim_data is None:
                    continue
                res = _simulate_silent(start_capital, sim_data, start_date, end_date)
                if not res or res.get("liquidation_date"):
                    continue
                if res.get('max_drawdown_pct', 100.0) / 100.0 > target_max_dd_decimal:
                    continue
                if res['end_capital'] > best_capital:
                    best_capital = res['end_capital']
                    best_files = list(combo)
                    best_result = res
        print()

    else:
        starts = candidate_files[:MAX_GREEDY_STARTS]
        # Geschätzte Gesamtsimulationen: starts × (1 + n Kandidaten pro Schritt × ~n/2 Schritte)
        estimated_total = len(starts) * (1 + n * (n // 2))
        sim_counter = {'done': 0, 'total': estimated_total, 'best_capital': best_capital,
                       'start_i': 0, 'start_n': len(starts)}

        print(f"\n2/2: Multi-Start-Greedy — Top {len(starts)} von {n} Kandidaten als Startpunkt...")
        for i, start_file in enumerate(starts, 1):
            sim_counter['start_i'] = i
            print(f"\n  Startpunkt {i}/{len(starts)}: {start_file}")
            files, result, sim_counter = _greedy_from(
                start_file, candidate_files, strategies_data,
                start_capital, start_date, end_date, target_max_dd_decimal, sim_counter
            )
            if result and result['end_capital'] > best_capital:
                best_capital = result['end_capital']
                sim_counter['best_capital'] = best_capital
                best_files = files
                best_result = result
                print(f"\n  ✓ Neues Optimum: {best_capital:.2f} USDT | DD: {best_result['max_drawdown_pct']:.2f}% | {len(best_files)} Strategien")
        print()

    if best_files is None:
        print(f"Kein Portfolio gefunden das Max DD <= {target_max_dd:.2f}% einhält.")
        return {"optimal_portfolio": [], "final_result": None}

    print(f"\nOptimum: {len(best_files)} Strategien | Endkapital: {best_capital:.2f} USDT | Max DD: {best_result['max_drawdown_pct']:.2f}%")

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
