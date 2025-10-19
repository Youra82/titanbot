# src/titanbot/analysis/show_results.py (Version für TitanBot SMC mit MaxDD)
import os
import sys
import json
import pandas as pd
from datetime import date
import logging
import argparse # Wichtig für Argument Parsing

# Logging etc. bleibt gleich
logging.getLogger('tensorflow').setLevel(logging.ERROR)
logging.getLogger('absl').setLevel(logging.ERROR)
import warnings
warnings.filterwarnings('ignore', category=UserWarning, module='keras')

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from titanbot.analysis.backtester import load_data, run_smc_backtest
from titanbot.analysis.portfolio_simulator import run_portfolio_simulation
# *** Portfolio Optimizer Import bleibt ***
from titanbot.analysis.portfolio_optimizer import run_portfolio_optimizer
from titanbot.utils.telegram import send_document

# --- run_single_analysis bleibt unverändert ---
def run_single_analysis(start_date, end_date, start_capital):
    print("--- TitanBot Ergebnis-Analyse (Einzel-Modus) ---")
    configs_dir = os.path.join(PROJECT_ROOT, 'src', 'titanbot', 'strategy', 'configs')
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
            strategy_name = f"{symbol} ({timeframe})"
            print(f"\nAnalysiere Ergebnisse für: {filename}...")
            data = load_data(symbol, timeframe, start_date, end_date)
            if data.empty:
                print(f"--> WARNUNG: Konnte keine Daten laden für {strategy_name}. Überspringe."); continue
            smc_params = config.get('strategy', {})
            risk_params = config.get('risk', {})
            result = run_smc_backtest(data.copy(), smc_params, risk_params, start_capital, verbose=False)
            all_results.append({
                "Strategie": strategy_name,
                "Trades": result.get('trades_count', 0),
                "Win Rate %": result.get('win_rate', 0),
                "PnL %": result.get('total_pnl_pct', -100),
                "Max DD %": result.get('max_drawdown_pct', 1.0) * 100,
                "Endkapital": result.get('end_capital', start_capital)
            })
        except Exception as e:
            print(f"--> FEHLER bei der Analyse von {filename}: {e}")
            continue
    if not all_results:
        print("\nKeine gültigen Ergebnisse zum Anzeigen gefunden."); return
    results_df = pd.DataFrame(all_results)
    results_df = results_df.sort_values(by="PnL %", ascending=False)
    pd.set_option('display.width', 1000); pd.set_option('display.max_columns', None)
    print("\n\n=========================================================================================");
    print(f"                        Zusammenfassung aller Einzelstrategien");
    print("=========================================================================================")
    pd.set_option('display.float_format', '{:.2f}'.format);
    print(results_df.to_string(index=False));
    print("=========================================================================================")


# *** Angepasst: Nimmt target_max_dd entgegen ***
def run_shared_mode(is_auto: bool, start_date, end_date, start_capital, target_max_dd: float):
    mode_name = "Automatische Portfolio-Optimierung" if is_auto else "Manuelle Portfolio-Simulation"
    print(f"--- TitanBot {mode_name} ---")
    if is_auto:
        print(f"Ziel: Maximaler Profit bei maximal {target_max_dd:.2f}% Drawdown.")

    configs_dir = os.path.join(PROJECT_ROOT, 'src', 'titanbot', 'strategy', 'configs')
    available_strategies = []
    if os.path.isdir(configs_dir):
        for filename in sorted(os.listdir(configs_dir)):
            if filename.startswith('config_') and filename.endswith('.json'):
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
        selected_files = available_strategies

    strategies_data = {}
    print("\nLade Daten für gewählte Strategien...")
    for filename in selected_files:
        try:
            with open(os.path.join(configs_dir, filename), 'r') as f: config = json.load(f)
            symbol = config['market']['symbol']
            timeframe = config['market']['timeframe']
            data = load_data(symbol, timeframe, start_date, end_date)
            if not data.empty:
                strategies_data[filename] = {
                    'symbol': symbol, 'timeframe': timeframe, 'data': data,
                    'smc_params': config.get('strategy', {}),
                    'risk_params': config.get('risk', {})
                }
            else:
                print(f"WARNUNG: Konnte Daten für {filename} nicht laden. Wird ignoriert.")
        except Exception as e:
             print(f"FEHLER beim Laden der Config/Daten für {filename}: {e}")

    if not strategies_data:
        print("Konnte für keine der gewählten Strategien Daten laden. Breche ab."); return

    equity_df = pd.DataFrame()
    csv_path = ""
    caption = ""

    try:
        if is_auto:
            # *** NEU: Übergebe target_max_dd an den Optimizer ***
            results = run_portfolio_optimizer(start_capital, strategies_data, start_date, end_date, target_max_dd)
            # *** ENDE NEU ***
            if results and 'final_result' in results and results['final_result'] is not None: # Prüfe ob Ergebnis None ist
                final_report = results['final_result']
                print("\n======================================================="); print("     Ergebnis der automatischen Portfolio-Optimierung"); print("=======================================================")
                print(f"Zeitraum: {start_date} bis {end_date}\nStartkapital: {start_capital:.2f} USDT")
                print(f"Bedingung: Max Drawdown <= {target_max_dd:.2f}%") # Zeige Bedingung an
                
                if results.get('optimal_portfolio'):
                     print("\nOptimales Portfolio gefunden (" + str(len(results['optimal_portfolio'])) + " Strategien):")
                     for strat_filename in results['optimal_portfolio']: print(f"  - {strat_filename}")
                else: # Fallback, wenn nur Einzelergebnis gefunden
                     print("\nBeste Einzelstrategie gefunden:")
                     # Versuche Dateinamen aus Resultat zu extrahieren (falls vorhanden)
                     strat_key = final_report.get('strategy_key', 'Unbekannt')
                     print(f"  - {strat_key}")

                print("\n--- Simulierte Performance dieses Portfolios/dieser Strategie ---")
                print(f"Endkapital:         {final_report['end_capital']:.2f} USDT"); print(f"Gesamt PnL:         {final_report['end_capital'] - start_capital:+.2f} USDT ({final_report['total_pnl_pct']:.2f}%)")
                print(f"Portfolio Max DD:   {final_report['max_drawdown_pct']:.2f}%")
                liq_date = final_report.get('liquidation_date')
                print(f"Liquidiert:         {'JA, am ' + liq_date.strftime('%Y-%m-%d') if liq_date else 'NEIN'}")

                csv_path = os.path.join(PROJECT_ROOT, 'optimal_portfolio_equity.csv')
                caption = f"Automatischer Portfolio-Optimierungsbericht (Max DD <= {target_max_dd:.1f}%)\nEndkapital: {final_report['end_capital']:.2f} USDT"
                equity_df = final_report.get('equity_curve')
            else:
                 print(f"\nKein Portfolio gefunden, das die Bedingung Max Drawdown <= {target_max_dd:.2f}% erfüllt.")

        # --- Manuelle Simulation bleibt größtenteils gleich ---
        else:
            sim_data = {v['symbol'] + "_" + v['timeframe']: v for k, v in strategies_data.items()}
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
        import traceback
        traceback.print_exc() # Gib detaillierten Fehler aus
        equity_df = pd.DataFrame()

    # --- Ergebnisse speichern und senden (bleibt gleich) ---
    if equity_df is not None and not equity_df.empty and csv_path:
        print("\n--- Export ---")
        try:
            export_cols = ['timestamp', 'equity', 'drawdown_pct']
            available_cols = [col for col in export_cols if col in equity_df.columns]
            # Ensure timestamp is the index before saving
            if 'timestamp' in equity_df.columns and not isinstance(equity_df.index, pd.DatetimeIndex):
                 equity_df['timestamp'] = pd.to_datetime(equity_df['timestamp'])
                 equity_df.set_index('timestamp', inplace=True, drop=False)
            
            # Wähle nur existierende Spalten für den Export
            equity_df_export = equity_df[available_cols].copy()
            # Falls timestamp nicht im Index war, füge es als Spalte hinzu
            if 'timestamp' not in equity_df_export.columns and isinstance(equity_df.index, pd.DatetimeIndex):
                 equity_df_export.insert(0, 'timestamp', equity_df.index)

            equity_df_export.to_csv(csv_path, index=False) # Index nicht mitspeichern
            print(f"✔ Details zur Equity-Kurve wurden nach '{os.path.basename(csv_path)}' exportiert.")
            print("=======================================================")
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
         print("\nPortfolio-Analyse fehlgeschlagen oder kein gültiges Portfolio gefunden, kein Export möglich.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', default='1', type=str, help="Analyse-Modus (1=Einzel, 2=Manuell, 3=Auto)")
    # *** NEU: Argument für Max Drawdown ***
    parser.add_argument('--target_max_drawdown', default=30.0, type=float, help="Ziel Max Drawdown % (nur für Modus 3)")
    args = parser.parse_args()

    print("\n--- Bitte Konfiguration für den Backtest festlegen ---")
    start_date = input(f"Startdatum (JJJJ-MM-TT) [Standard: 2023-01-01]: ") or "2023-01-01"
    end_date = input(f"Enddatum (JJJJ-MM-TT) [Standard: Heute]: ") or date.today().strftime("%Y-%m-%d")
    start_capital = int(input(f"Startkapital in USDT eingeben [Standard: 1000]: ") or 1000)
    print("--------------------------------------------------")

    if args.mode == '2':
        # Übergebe Dummy MaxDD für manuelle Simulation
        run_shared_mode(is_auto=False, start_date=start_date, end_date=end_date, start_capital=start_capital, target_max_dd=999.0)
    elif args.mode == '3':
        # *** NEU: Übergebe das geparste target_max_drawdown ***
        run_shared_mode(is_auto=True, start_date=start_date, end_date=end_date, start_capital=start_capital, target_max_dd=args.target_max_drawdown)
    else: # Modus 1 (default)
        run_single_analysis(start_date=start_date, end_date=end_date, start_capital=start_capital)
