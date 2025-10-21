# src/titanbot/analysis/portfolio_optimizer.py (Version für TitanBot SMC mit MaxDD Constraint & Coin-Kollisionsschutz)
import pandas as pd
import itertools
from tqdm import tqdm
import sys
import os
import json # Fürs Speichern
import numpy as np # Für np.nan

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from titanbot.analysis.portfolio_simulator import run_portfolio_simulation

# *** Angepasst: Nimmt target_max_dd entgegen ***
def run_portfolio_optimizer(start_capital, strategies_data, start_date, end_date, target_max_dd: float):
    """
    Findet die Kombination von SMC-Strategien, die das höchste Endkapital liefert,
    während der maximale Drawdown unter dem Zielwert (`target_max_dd`) bleibt UND jeder Coin nur einmal vorkommt.
    Verwendet einen modifizierten Greedy-Algorithmus.
    """
    print(f"\n--- Starte automatische Portfolio-Optimierung (SMC) mit Max DD <= {target_max_dd:.2f}% & ohne Coin-Kollisionen ---")
    target_max_dd_decimal = target_max_dd / 100.0 # Umrechnung in Dezimalzahl für Vergleiche

    if not strategies_data:
        print("Keine Strategien zum Optimieren gefunden.")
        return None

    # --- 1. Analysiere Einzel-Performance & filtere nach Max DD ---
    print("1/3: Analysiere Einzel-Performance & filtere nach Max DD...")
    single_strategy_results = []

    for filename, strat_data in tqdm(strategies_data.items(), desc="Bewerte Einzelstrategien"):
        strategy_key = f"{strat_data['symbol']}_{strat_data['timeframe']}"
        sim_data = {strategy_key: strat_data}
        if 'data' not in strat_data or strat_data['data'].empty:
            print(f"WARNUNG: Keine Daten für {filename} in Einzelanalyse.")
            continue

        result = run_portfolio_simulation(start_capital, sim_data, start_date, end_date)

        if result and not result.get("liquidation_date"):
            # Max DD aus Ergebnis holen (als Dezimalzahl)
            # Nutze 1.0 (100%) als Fallback, wenn Schlüssel fehlt
            actual_max_dd = result.get('max_drawdown_pct', 100.0) / 100.0

            # *** NEU: Filter nach target_max_dd ***
            if actual_max_dd <= target_max_dd_decimal:
                # Füge nur Strategien hinzu, die die Bedingung erfüllen
                single_strategy_results.append({
                    'filename': filename,
                    'result': result # Speichere das vollständige Ergebnis
                })
            # else:
                # Optional: Logge verworfene Strategien
                # print(f"Info: Einzelstrategie {filename} verworfen (Max DD {actual_max_dd*100:.2f}% > {target_max_dd:.2f}%)")
        # else:
            # Optional: Logge liquidierte Strategien
            # print(f"Info: Einzelstrategie {filename} führte zur Liquidation.")


    if not single_strategy_results:
        print(f"Keine einzige Strategie erfüllte die Bedingung Max DD <= {target_max_dd:.2f}%. Portfolio-Optimierung nicht möglich.")
        return {"optimal_portfolio": [], "final_result": None} # Gebe leeres Ergebnis zurück

    # --- 2. Finde den "Star-Spieler" basierend auf HÖCHSTEM PROFIT unter den gefilterten ---
    # Sortiere nach Endkapital (absteigend)
    single_strategy_results.sort(key=lambda x: x['result']['end_capital'], reverse=True)

    best_portfolio_files = [single_strategy_results[0]['filename']]
    best_portfolio_result = single_strategy_results[0]['result']
    best_end_capital = best_portfolio_result['end_capital'] # Merke dir das beste Kapital

    # Pool der verbleibenden Kandidaten (alle, außer dem besten)
    candidate_pool = [res['filename'] for res in single_strategy_results[1:]]

    print(f"2/3: Beste Einzelstrategie (unter Max DD): {best_portfolio_files[0]} (Endkapital: {best_end_capital:.2f} USDT, Max DD: {best_portfolio_result['max_drawdown_pct']:.2f}%)")
    print("3/3: Suche die besten Team-Kollegen...")

    # --- 3. Greedy-Algorithmus: Füge schrittweise die Strategie hinzu, die den Profit MAXIMIERT, ohne Max DD zu verletzen UND ohne Coin-Kollision ---

    selected_coins = set() # NEU: Set für bereits ausgewählte Coins
    # Füge den Coin der besten Einzelstrategie hinzu (falls vorhanden)
    if best_portfolio_files: # NEU
        initial_best_strat_data = strategies_data.get(best_portfolio_files[0]) # NEU
        if initial_best_strat_data: # NEU
            # Extrahiere Coin-Symbol (z.B. BTC aus BTC/USDT:USDT)
            initial_coin = initial_best_strat_data['symbol'].split('/')[0] # NEU
            selected_coins.add(initial_coin) # NEU

    while True:
        best_next_addition = None
        best_capital_with_addition = best_end_capital # Starte mit dem Kapital des aktuellen besten Portfolios
        current_best_result_for_addition = best_portfolio_result # Merke dir das Ergebnis dieser Runde

        progress_bar = tqdm(candidate_pool, desc=f"Teste Team mit {len(best_portfolio_files)+1} Mitgliedern")
        for candidate_file in progress_bar:

            # --- START: NEUER CODE ZUR KOLLISIONSPRÜFUNG ---
            candidate_strat_data = strategies_data.get(candidate_file)
            if not candidate_strat_data:
                continue # Überspringe, falls Daten für Kandidat fehlen

            candidate_coin = candidate_strat_data['symbol'].split('/')[0]

            # Prüfe, ob der Coin dieses Kandidaten bereits im Portfolio ist
            if candidate_coin in selected_coins:
                continue # Überspringe diesen Kandidaten, da der Coin schon vorhanden ist
            # --- ENDE: NEUER CODE ---

            # Bestehender Code:
            current_team_files = best_portfolio_files + [candidate_file]

            # Eindeutigkeitsprüfung (gleicher Coin/Timeframe - sollte durch obige Prüfung unnötig sein, aber sicher ist sicher)
            unique_check = set()
            is_valid_team = True
            for f in current_team_files:
                strat_info = strategies_data.get(f)
                if not strat_info: is_valid_team = False; break
                key = strat_info['symbol'] + strat_info['timeframe']
                if key in unique_check: is_valid_team = False; break
                unique_check.add(key)
            if not is_valid_team: continue

            # Daten für Simulator zusammenstellen
            current_team_data = {}
            valid_data_for_sim = True
            for fname in current_team_files:
                strat_d = strategies_data.get(fname)
                if strat_d and 'data' in strat_d and not strat_d['data'].empty:
                    sim_key = f"{strat_d['symbol']}_{strat_d['timeframe']}"
                    current_team_data[sim_key] = strat_d
                else:
                    valid_data_for_sim = False; break
            if not valid_data_for_sim: continue

            # Portfolio simulieren
            result = run_portfolio_simulation(start_capital, current_team_data, start_date, end_date)

            # Prüfen ob Ergebnis gültig UND Max DD eingehalten wird
            if result and not result.get("liquidation_date"):
                actual_max_dd = result.get('max_drawdown_pct', 100.0) / 100.0

                # *** NEUE BEDINGUNG: Prüfe Max DD UND ob Endkapital besser ist ***
                if actual_max_dd <= target_max_dd_decimal and result['end_capital'] > best_capital_with_addition:
                    # Dieses Team ist besser als das bisher beste dieser Runde
                    best_capital_with_addition = result['end_capital']
                    best_next_addition = candidate_file
                    current_best_result_for_addition = result # Aktualisiere das beste Ergebnis dieser Runde

        # Prüfe, ob eine Verbesserung gefunden wurde (best_next_addition ist nicht None)
        if best_next_addition:
            # Eine bessere Kombination wurde gefunden
            print(f"-> Füge hinzu: {best_next_addition} (Neues Kapital: {best_capital_with_addition:.2f} USDT, Max DD: {current_best_result_for_addition['max_drawdown_pct']:.2f}%)")
            best_portfolio_files.append(best_next_addition)

            # --- START: NEUER CODE ZUM AKTUALISIEREN DES SETS ---
            added_strat_data = strategies_data.get(best_next_addition)
            if added_strat_data:
                added_coin = added_strat_data['symbol'].split('/')[0]
                selected_coins.add(added_coin)
            # --- ENDE: NEUER CODE ---

            # Bestehender Code:
            best_end_capital = best_capital_with_addition # Aktualisiere globales bestes Kapital
            best_portfolio_result = current_best_result_for_addition # Übernehme das beste Ergebnis
            candidate_pool.remove(best_next_addition) # Entferne aus Kandidaten
        else:
            # Keine weitere Verbesserung durch Hinzufügen möglich oder alle Kandidaten verletzen Max DD/Coin-Constraint
            print("Keine weitere Verbesserung des Profits (unter Einhaltung des Max DD & ohne Coin-Kollision) durch Hinzufügen von Strategien gefunden. Optimierung beendet.")
            break # Verlasse die while-Schleife

    # --- Ergebnisse speichern ---
    try:
        results_dir = os.path.join(PROJECT_ROOT, 'artifacts', 'results')
        os.makedirs(results_dir, exist_ok=True)
        output_path = os.path.join(results_dir, 'optimization_results.json')
        # Speichere die Dateinamen des finalen Portfolios
        save_data = {"optimal_portfolio": best_portfolio_files}
        with open(output_path, 'w') as f:
            json.dump(save_data, f, indent=4)
        print(f"Optimales Portfolio (Max DD <= {target_max_dd:.2f}%) in '{output_path}' gespeichert.")
    except Exception as e:
        print(f"Fehler beim Speichern der Optimierungsergebnisse: {e}")


    # Gib das finale beste Portfolio und sein Ergebnis zurück
    return {"optimal_portfolio": best_portfolio_files, "final_result": best_portfolio_result}
