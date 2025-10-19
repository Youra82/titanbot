# src/titanbot/analysis/show_results.py (Version für TitanBot SMC)
import os
import sys
import json
import pandas as pd
from datetime import date
import logging
import argparse

# Unterdrücke Tensorflow/Keras Lade-Warnungen (obwohl wir es nicht mehr nutzen)
logging.getLogger('tensorflow').setLevel(logging.ERROR)
logging.getLogger('absl').setLevel(logging.ERROR)
import warnings
warnings.filterwarnings('ignore', category=UserWarning, module='keras')

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

# *** Korrigierte Imports: Neuer Backtester, TitanBot-Pfade ***
from titanbot.analysis.backtester import load_data, run_smc_backtest # <-- HIER IST DIE ÄNDERUNG
# *** ANN/KI Imports entfernt ***
from titanbot.analysis.portfolio_simulator import run_portfolio_simulation
from titanbot.analysis.portfolio_optimizer import run_portfolio_optimizer
# from titanbot.analysis.evaluator import evaluate_dataset # Wird hier nicht direkt benötigt
from titanbot.utils.telegram import send_document

def run_single_analysis(start_date, end_date, start_capital):
    print("--- TitanBot Ergebnis-Analyse (Einzel-Modus) ---")

    # *** Korrigierter Configs-Pfad ***
    configs_dir = os.path.join(PROJECT_ROOT, 'src', 'titanbot', 'strategy', 'configs')
    # *** Model-Pfad entfernt ***

    all_results = []
    config_files = sorted([f for f in os.listdir(configs_dir) if f.startswith('config_') and f.endswith('.json')])

    if not config_files:
        print("\nKeine gültigen Konfigurationen zum Analysieren gefunden."); return

    print(f"Zeitraum: {start_date} bis {end_date} | Startkapital: {start_capital} USDT")

    for filename in config_files:
        config_path = os.path.join(configs_dir, filename)
        if not os.path.exists(config_path): continue

        try:
            with open(config_path, 'r') as f: config = json.load(f)

            symbol = config['market']['symbol']
            timeframe = config['market']['timeframe']
            strategy_name = f"{symbol} ({timeframe})" # Einfacher Name

            print(f"\nAnalysiere Ergebnisse für: {filename}...")

            # --- Daten laden ---
            data = load_data(symbol, timeframe, start_date, end_date)
            if data.empty:
                print(f"--> WARNUNG: Konnte keine Daten laden für {strategy_name}. Überspringe."); continue

            # --- Parameter für den SMC-Backtester vorbereiten ---
            smc_params = config.get('strategy', {})
            risk_params = config.get('risk', {})

            # --- SMC-Backtest ausführen ---
            result = run_smc_backtest(data.copy(), smc_params, risk_params, start_capital, verbose=False) # verbose=False für saubere Ausgabe

            # --- Ergebnisse sammeln ---
            all_results.append({
                "Strategie": strategy_name,
                "Trades": result.get('trades_count', 0),
                "Win Rate %": result.get('win_rate', 0),
                "PnL %": result.get('total_pnl_pct', -100),
                "Max DD %": result.get('max_drawdown_pct', 1.0) * 100, # In Prozent umrechnen
                "Endkapital": result.get('end_capital', start_capital)
            })

        except Exception as e:
            print(f"--> FEHLER bei der Analyse von {filename}: {e}")
            continue # Nächste Config versuchen

    if not all_results:
        print("\nKeine gültigen Ergebnisse zum Anzeigen gefunden."); return

    # --- Ergebnisse anzeigen ---
    results_df = pd.DataFrame(all_results)
    results_df = results_df.sort_values(by="PnL %", ascending=False)

    pd.set_option('display.width', 1000); pd.set_option('display.max_columns', None)
    print("\n\n=========================================================================================");
    print(f"                        Zusammenfassung aller Einzelstrategien");
    print("=========================================================================================")
    pd.set_option('display.float_format', '{:.2f}'.format);
    print(results_df.to_string(index=False));
    print("=========================================================================================")

def run_shared_mode(is_auto: bool, start_date, end_date, start_capital):
    mode_name = "Automatische Portfolio-Optimierung" if is_auto else "Manuelle Portfolio-Simulation"
    print(f"--- TitanBot {mode_name} ---")

    # *** Korrigierter Configs-Pfad ***
    configs_dir = os.path.join(PROJECT_ROOT, 'src', 'titanbot', 'strategy', 'configs')
    # *** Model-Pfad entfernt ***

    available_strategies = []
    if os.path.isdir(configs_dir):
        for filename in sorted(os.listdir(configs_dir)):
            if filename.startswith('config_') and filename.endswith('.json'):
                # Für SMC ist keine Modellprüfung nötig, jede Config ist potenziell gültig
                 available_strategies.append(filename)

    if not available_strategies:
        print("Keine optimierten Strategien (Configs) gefunden."); return

    selected_files = []
    if not is_auto:
        print("\nVerfügbare Strategien:")
        for i, name in enumerate(available_strategies): print(f"  {i+1}) {name}")
        selection = input("\nWelche Strategien sollen simuliert werden? (Zahlen mit Komma, z.B. 1,3,4 oder 'alle'): ")
        try:
            if selection.lower() == 'alle': selected_files = available_strategies
            else: selected_files = [available_strategies[int(i.strip()) - 1] for i in selection.split(',')]
        except (ValueError, IndexError): print("Ungültige Auswahl. Breche ab."); return
    else:
        selected_files = available_strategies # Optimizer testet alle verfügbaren

    strategies_data = {}
    print("\nLade Daten für gewählte Strategien...")
    for filename in selected_files:
        try:
            with open(os.path.join(configs_dir, filename), 'r') as f: config = json.load(f)

            symbol = config['market']['symbol']
            timeframe = config['market']['timeframe']

            # --- Daten laden ---
            data = load_data(symbol, timeframe, start_date, end_date)

            # --- Strategie-Daten vorbereiten (ohne Modell/Scaler) ---
            if not data.empty:
                strategies_data[filename] = {
                    'symbol': symbol,
                    'timeframe': timeframe,
                    'data': data,
                    # Übergebe die Parameter direkt
                    'smc_params': config.get('strategy', {}),
                    'risk_params': config.get('risk', {})
                }
            else:
                print(f"WARNUNG: Konnte Daten für {filename} nicht laden. Wird ignoriert.")
        except Exception as e:
             print(f"FEHLER beim Laden der Config/Daten für {filename}: {e}")

    if not strategies_data:
        print("Konnte für keine der gewählten Strategien Daten laden. Breche ab."); return

    # --- Portfolio Simulation oder Optimierung ---
    equity_df = pd.DataFrame() # Initialisiere ein leeres DataFrame
    csv_path = ""
    caption = ""

    try:
        if is_auto:
            results = run_portfolio_optimizer(start_capital, strategies_data, start_date, end_date)
            if results and 'final_result' in results:
                final_report = results['final_result']
                print("\n======================================================="); print("     Ergebnis der automatischen Portfolio-Optimierung"); print("=======================================================")
                print(f"Zeitraum: {start_date} bis {end_date}\nStartkapital: {start_capital:.2f} USDT")
                print("\nOptimales Portfolio gefunden (" + str(len(results['optimal_portfolio'])) + " Strategien):")
                for strat_filename in results['optimal_portfolio']: print(f"  - {strat_filename}")
                print("\n--- Simulierte Performance dieses optimalen Portfolios ---")
                print(f"Endkapital:         {final_report['end_capital']:.2f} USDT"); print(f"Gesamt PnL:         {final_report['end_capital'] - start_capital:+.2f} USDT ({final_report['total_pnl_pct']:.2f}%)")
                print(f"Portfolio Max DD:   {final_report['max_drawdown_pct']:.2f}%")
                # Liquidation Check (optional, falls vom Simulator zurückgegeben)
                liq_date = final_report.get('liquidation_date')
                print(f"Liquidiert:         {'JA, am ' + liq_date.strftime('%Y-%m-%d') if liq_date else 'NEIN'}")

                csv_path = os.path.join(PROJECT_ROOT, 'optimal_portfolio_equity.csv')
                caption = f"Automatischer Portfolio-Optimierungsbericht\nEndkapital: {final_report['end_capital']:.2f} USDT"
                equity_df = final_report.get('equity_curve')
        else:
            # Stelle die Daten für den Simulator zusammen (der erwartet Symbol als Key)
            # WICHTIG: Stelle sicher, dass dein portfolio_simulator.py angepasst wurde,
            # um 'smc_params' und 'risk_params' zu akzeptieren!
            sim_data = {v['symbol'] + "_" + v['timeframe']: v for k, v in strategies_data.items()} # Eindeutiger Key

            results = run_portfolio_simulation(start_capital, sim_data, start_date, end_date)
            if results:
                print("\n======================================================="); print("           Portfolio-Simulations-Ergebnis"); print("=======================================================")
                print(f"Zeitraum: {start_date} bis {end_date}\nStartkapital: {results['start_capital']:.2f} USDT")
                print("\n--- Gesamt-Performance ---")
                print(f"Endkapital:         {results['end_capital']:.2f} USDT"); print(f"Gesamt PnL:         {results['end_capital'] - results['start_capital']:+.2f} USDT ({results['total_pnl_pct']:.2f}%)")
                print(f"Anzahl Trades:      {results['trade_count']}"); print(f"Win-Rate:           {results['win_rate']:.2f}%")
                print(f"Portfolio Max DD:   {results['max_drawdown_pct']:.2f}% am {results['max_drawdown_date'].strftime('%Y-%m-%d') if results['max_drawdown_date'] else 'N/A'}")
                liq_date = results.get('liquidation_date')
                print(f"Liquidiert:         {'JA, am ' + liq_date.strftime('%Y-%m-%d') if liq_date else 'NEIN'}")

                csv_path = os.path.join(PROJECT_ROOT, 'manual_portfolio_equity.csv')
                caption = f"Manueller Portfolio-Simulationsbericht\nEndkapital: {results['end_capital']:.2f} USDT"
                equity_df = results.get('equity_curve')

    except Exception as e:
        print(f"\nFEHLER während der Portfolio-Analyse: {e}")
        # Optional: Detaillierteren Traceback ausgeben
        # import traceback
        # traceback.print_exc()
        equity_df = pd.DataFrame() # Stelle sicher, dass es leer ist

    # --- Ergebnisse speichern und senden ---
    if equity_df is not None and not equity_df.empty and csv_path:
        print("\n--- Export ---")
        try:
            # Stelle sicher, dass die benötigten Spalten vorhanden sind
            export_cols = ['timestamp', 'equity', 'drawdown_pct']
            available_cols = [col for col in export_cols if col in equity_df.columns]
            equity_df[available_cols].to_csv(csv_path, index=False)
            print(f"✔ Details zur Equity-Kurve wurden nach '{os.path.basename(csv_path)}' exportiert.")
            print("=======================================================")

            # An Telegram senden
            try:
                with open(os.path.join(PROJECT_ROOT, 'secret.json'), 'r') as f: secrets = json.load(f)
                telegram_config = secrets.get('telegram', {})
                if telegram_config.get('bot_token'):
                    print("Sende Bericht an Telegram...")
                    send_document(telegram_config.get('bot_token'), telegram_config.get('chat_id'), csv_path, caption)
                    print("✔ Bericht wurde erfolgreich an Telegram gesendet.")
            except Exception as e_tg:
                print(f"ⓘ Konnte Bericht nicht an Telegram senden: {e_tg}")
        except Exception as e_csv:
            print(f"FEHLER beim Speichern der CSV '{csv_path}': {e_csv}")
    elif csv_path:
         print(f"\nKeine Equity-Daten zum Exportieren für '{os.path.basename(csv_path)}' vorhanden.")
    else:
         print("\nPortfolio-Analyse fehlgeschlagen, kein Export möglich.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', default='1', type=str)
    args = parser.parse_args()

    print("\n--- Bitte Konfiguration für den Backtest festlegen ---")
    start_date = input(f"Startdatum (JJJJ-MM-TT) [Standard: 2023-01-01]: ") or "2023-01-01"
    end_date = input(f"Enddatum (JJJJ-MM-TT) [Standard: Heute]: ") or date.today().strftime("%Y-%m-%d")
    start_capital = int(input(f"Startkapital in USDT eingeben [Standard: 1000]: ") or 1000)
    print("--------------------------------------------------")

    if args.mode == '2':
        run_shared_mode(is_auto=False, start_date=start_date, end_date=end_date, start_capital=start_capital)
    elif args.mode == '3':
        run_shared_mode(is_auto=True, start_date=start_date, end_date=end_date, start_capital=start_capital)
    else: # Modus 1 (default)
        run_single_analysis(start_date=start_date, end_date=end_date, start_capital=start_capital)
