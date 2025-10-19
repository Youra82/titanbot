# src/titanbot/analysis/portfolio_optimizer.py (Version für TitanBot SMC)
import pandas as pd
import itertools
from tqdm import tqdm
import sys
import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

# *** Korrigierter Importpfad ***
from titanbot.analysis.portfolio_simulator import run_portfolio_simulation

def run_portfolio_optimizer(start_capital, strategies_data, start_date, end_date):
    """
    Findet die beste Kombination von SMC-Strategien, um die risikoadjustierte Rendite
    (Score = PnL / MaxDD) zu maximieren, ohne dabei liquidiert zu werden,
    unter Verwendung eines Greedy-Algorithmus.
    'strategies_data' ist ein Dict {filename: {'symbol': ..., 'timeframe': ..., 'data': ..., 'smc_params': ..., 'risk_params': ...}}
    """
    print("\n--- Starte automatische Portfolio-Optimierung (SMC)... ---")

    if not strategies_data:
        print("Keine Strategien zum Optimieren gefunden.")
        return None

    print("1/3: Analysiere Einzel-Performance jeder Strategie...")
    single_strategy_results = []

    # Der Schlüssel im übergebenen strategies_data ist der Dateiname (z.B. config_...json)
    for filename, strat_data in tqdm(strategies_data.items(), desc="Bewerte Einzelstrategien"):
        # Übergebe dem Simulator die Daten für die eine Strategie.
        # Der Simulator erwartet Keys im Format Symbol_Timeframe
        strategy_key = f"{strat_data['symbol']}_{strat_data['timeframe']}"
        sim_data = {strategy_key: strat_data}

        # Stelle sicher, dass data vorhanden ist
        if 'data' not in strat_data or strat_data['data'].empty:
            print(f"WARNUNG: Keine Daten für {filename} in Einzelanalyse.")
            continue

        result = run_portfolio_simulation(start_capital, sim_data, start_date, end_date)

        if result and not result.get("liquidation_date"):
            # Wir verwenden eine risikoadjustierte Rendite als Score
            # (z.B. Calmar Ratio: PnL / MaxDD)
            max_dd_pct = result.get('max_drawdown_pct', 100.0) # Standard 100% DD, falls nicht vorhanden
            if max_dd_pct <= 0: max_dd_pct = 1.0 # Vermeide Division durch Null, setze auf minimalen DD
            
            # Teile PnL durch MaxDD in Prozent (nicht als Dezimalzahl)
            score = result['total_pnl_pct'] / max_dd_pct 
            
            single_strategy_results.append({
                'filename': filename,
                'score': score,
                'result': result # Speichere das vollständige Ergebnis
            })
        else:
             print(f"Einzelstrategie {filename} führte zur Liquidation oder fehlgeschlagen.")


    if not single_strategy_results:
        print("Keine einzige Strategie war für sich allein überlebensfähig. Portfolio-Optimierung nicht möglich.")
        return None

    # Sortiere nach dem besten Score, um den "Star-Spieler" zu finden
    single_strategy_results.sort(key=lambda x: x['score'], reverse=True)

    best_portfolio_files = [single_strategy_results[0]['filename']]
    best_portfolio_score = single_strategy_results[0]['score']
    best_portfolio_result = single_strategy_results[0]['result']

    # Pool der verbleibenden Kandidaten
    candidate_pool = [res['filename'] for res in single_strategy_results[1:]]

    print(f"2/3: Star-Spieler gefunden: {best_portfolio_files[0]} (Score: {best_portfolio_score:.2f})")
    print("3/3: Suche die besten Team-Kollegen...")

    # Greedy-Algorithmus: Füge schrittweise die beste nächste Strategie hinzu
    while True:
        best_next_addition = None
        best_score_with_addition = best_portfolio_score
        current_best_result_for_addition = best_portfolio_result # Merke dir das beste Ergebnis dieser Runde

        progress_bar = tqdm(candidate_pool, desc=f"Teste Team mit {len(best_portfolio_files)+1} Mitgliedern")
        for candidate_file in progress_bar:
            current_team_files = best_portfolio_files + [candidate_file]

            # Stelle sicher, dass keine Duplikate (gleicher Coin/Timeframe) im Team sind
            unique_check = set()
            is_valid_team = True
            for f in current_team_files:
                strat_info = strategies_data.get(f)
                if not strat_info: # Sollte nicht passieren, aber sicher ist sicher
                    is_valid_team = False
                    break
                key = strat_info['symbol'] + strat_info['timeframe']
                if key in unique_check:
                    is_valid_team = False
                    break
                unique_check.add(key)

            if not is_valid_team:
                # print(f"Überspringe ungültiges Team: {current_team_files}") # Zum Debuggen
                continue

            # Stelle die Daten für den Simulator zusammen
            # Der Simulator erwartet Keys im Format Symbol_Timeframe
            current_team_data = {}
            valid_data_for_sim = True
            for fname in current_team_files:
                 strat_d = strategies_data.get(fname)
                 if strat_d and 'data' in strat_d and not strat_d['data'].empty:
                      sim_key = f"{strat_d['symbol']}_{strat_d['timeframe']}"
                      current_team_data[sim_key] = strat_d
                 else:
                      valid_data_for_sim = False
                      print(f"WARNUNG: Fehlende Daten für {fname} im Team-Test.")
                      break # Dieses Team kann nicht simuliert werden

            if not valid_data_for_sim:
                continue

            result = run_portfolio_simulation(start_capital, current_team_data, start_date, end_date)

            if result and not result.get("liquidation_date"):
                max_dd_pct = result.get('max_drawdown_pct', 100.0)
                if max_dd_pct <= 0: max_dd_pct = 1.0
                score = result['total_pnl_pct'] / max_dd_pct

                if score > best_score_with_addition:
                    best_score_with_addition = score
                    best_next_addition = candidate_file
                    current_best_result_for_addition = result # Aktualisiere das beste Ergebnis

        # Prüfe, ob eine Verbesserung gefunden wurde
        if best_next_addition:
            print(f"-> Füge hinzu: {best_next_addition} (Neuer Score: {best_score_with_addition:.2f})")
            best_portfolio_files.append(best_next_addition)
            best_portfolio_score = best_score_with_addition
            best_portfolio_result = current_best_result_for_addition # Übernehme das beste Ergebnis
            candidate_pool.remove(best_next_addition) # Entferne aus Kandidaten
        else:
            # Keine weitere Verbesserung möglich, der Algorithmus endet hier.
            print("Keine weitere Verbesserung durch Hinzufügen von Strategien gefunden. Optimierung beendet.")
            break # Verlasse die while-Schleife

    # Speichere das Ergebnis im artifacts Verzeichnis (optional)
    try:
        results_dir = os.path.join(PROJECT_ROOT, 'artifacts', 'results')
        os.makedirs(results_dir, exist_ok=True)
        output_path = os.path.join(results_dir, 'optimization_results.json')
        # Speichere nur die Dateinamen des optimalen Portfolios
        save_data = {"optimal_portfolio": best_portfolio_files}
        with open(output_path, 'w') as f:
            json.dump(save_data, f, indent=4)
        print(f"Optimales Portfolio in '{output_path}' gespeichert.")
    except Exception as e:
        print(f"Fehler beim Speichern der Optimierungsergebnisse: {e}")


    # Gib das vollständige Ergebnis des besten Portfolios zurück
    return {"optimal_portfolio": best_portfolio_files, "final_result": best_portfolio_result}
