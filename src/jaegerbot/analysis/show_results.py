# src/jaegerbot/analysis/show_results.py (Version 2 - Korrigiert)
import os
import sys
import json
import pandas as pd
from datetime import date
import logging
import argparse

logging.getLogger('tensorflow').setLevel(logging.ERROR)
logging.getLogger('absl').setLevel(logging.ERROR)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from jaegerbot.analysis.backtester import load_data, run_ann_backtest
from jaegerbot.utils.ann_model import create_ann_features, load_model_and_scaler
from jaegerbot.analysis.portfolio_simulator import run_portfolio_simulation
from jaegerbot.analysis.portfolio_optimizer import run_portfolio_optimizer
from jaegerbot.analysis.evaluator import evaluate_dataset
from jaegerbot.utils.telegram import send_document

def run_single_analysis(start_date, end_date, start_capital):
    print("--- JaegerBot Ergebnis-Analyse (Einzel-Modus) ---")
    # ... (der Rest dieser Funktion bleibt unverändert, hier gekürzt zur Übersicht)
    configs_dir = os.path.join(PROJECT_ROOT, 'src', 'jaegerbot', 'strategy', 'configs')
    models_dir = os.path.join(PROJECT_ROOT, 'artifacts', 'models')
    all_results = []
    config_files = sorted([f for f in os.listdir(configs_dir) if f.startswith('config_') and f.endswith('.json')])
    if not config_files:
        print("\nKeine gültigen Konfigurationen zum Analysieren gefunden."); return
    for filename in config_files:
        config_path = os.path.join(configs_dir, filename)
        if not os.path.exists(config_path): continue
        with open(config_path, 'r') as f: config = json.load(f)
        strategy_name_base = filename.replace('config_', '').replace('.json', '').replace('_macd', '')
        result_summary = next((item for item in all_results if item.get("id") == strategy_name_base), None)
        if result_summary is None:
            result_summary = {"id": strategy_name_base, "Strategie": f"{config['market']['symbol']} ({config['market']['timeframe']})"}
            all_results.append(result_summary)
        symbol, timeframe = config['market']['symbol'], config['market']['timeframe']
        print(f"\nAnalysiere Ergebnisse für: {filename}...")
        safe_filename = f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"
        model_paths = {'model': os.path.join(models_dir, f'ann_predictor_{safe_filename}.h5'), 'scaler': os.path.join(models_dir, f'ann_scaler_{safe_filename}.joblib')}
        if not os.path.exists(model_paths['model']):
            print(f"--> WARNUNG: Modell nicht gefunden. Überspringe."); continue
        data = load_data(symbol, timeframe, start_date, end_date)
        if data.empty:
            print(f"--> WARNUNG: Konnte keine Daten laden. Überspringe."); continue
        params = {**config.get('strategy', {}), **config.get('risk', {})}
        behavior = config.get('behavior', {}); is_macd_run = behavior.get('use_macd_trend_filter', False)
        result = run_ann_backtest(data.copy(), params, model_paths, start_capital, use_macd_filter=is_macd_run, timeframe=timeframe, verbose=not is_macd_run)
        if is_macd_run:
            result_summary.update({"Trades (ON)": result['trades_count'], "PnL % (ON)": result['total_pnl_pct'], "Max DD % (ON)": result['max_drawdown_pct'] * 100, "Endkapital (ON)": result['end_capital']})
        else:
            result_summary.update({"Trades (OFF)": result['trades_count'], "PnL % (OFF)": result['total_pnl_pct'], "Max DD % (OFF)": result['max_drawdown_pct'] * 100, "Endkapital (OFF)": result['end_capital']})
    if not all_results:
        print("\nKeine gültigen Konfigurationen mehr übrig."); return
    results_df = pd.DataFrame(all_results).drop(columns=['id'])
    for col in ["Trades (ON)", "PnL % (ON)", "Max DD % (ON)", "Endkapital (ON)"]:
        if col not in results_df.columns: results_df[col] = pd.NA
        results_df[col] = pd.to_numeric(results_df[col], errors='coerce')
    sort_by_col = "PnL % (ON)" if "PnL % (ON)" in results_df.columns and not results_df["PnL % (ON)"].isna().all() else "PnL % (OFF)"
    results_df = results_df.sort_values(by=sort_by_col, ascending=False)
    display_columns = ["Strategie", "Trades (OFF)", "Trades (ON)", "PnL % (OFF)", "PnL % (ON)", "Max DD % (OFF)", "Max DD % (ON)", "Endkapital (OFF)", "Endkapital (ON)"]
    existing_columns = [col for col in display_columns if col in results_df.columns]
    pd.set_option('display.width', 1000); pd.set_option('display.max_columns', None)
    print("\n\n======================================================================================================================="); print(f"     Zusammenfassung (Startkapital: {start_capital} USDT) - Vergleich mit/ohne MACD-Filter"); print("=======================================================================================================================")
    pd.set_option('display.float_format', '{:.2f}'.format); print(results_df.fillna('-').to_string(index=False, columns=existing_columns)); print("=======================================================================================================================")

def run_shared_mode(is_auto: bool, start_date, end_date, start_capital):
    mode_name = "Automatische Portfolio-Optimierung" if is_auto else "Manuelle Portfolio-Simulation"
    print(f"--- JaegerBot {mode_name} ---")
    # ... (der Code zum Laden der Strategien bleibt unverändert, hier gekürzt)
    configs_dir = os.path.join(PROJECT_ROOT, 'src', 'jaegerbot', 'strategy', 'configs')
    models_dir = os.path.join(PROJECT_ROOT, 'artifacts', 'models')
    available_strategies = []
    if os.path.isdir(configs_dir):
        for filename in sorted(os.listdir(configs_dir)):
            if filename.startswith('config_') and filename.endswith('.json'):
                base_name = filename.replace('config_', '').replace('.json', '').replace('_macd', '')
                try:
                    parts = base_name.split('_'); timeframe = parts[-1]; symbol_part = "_".join(parts[:-1])
                    model_name = f"ann_predictor_{symbol_part}_{timeframe}.h5"
                    if os.path.exists(os.path.join(models_dir, model_name)):
                        available_strategies.append(filename)
                except IndexError: continue
    if not available_strategies:
        print("Keine optimierten Strategien gefunden."); return
    selected_files = []
    if not is_auto:
        print("\nVerfügbare Strategien:")
        for i, name in enumerate(available_strategies): print(f"  {i+1}) {name}")
        selection = input("\nWelche Strategien sollen simuliert werden? (Zahlen mit Komma, z.B. 1,3,4 oder 'alle'): ")
        try:
            if selection.lower() == 'alle': selected_files = available_strategies
            else: selected_files = [available_strategies[int(i.strip()) - 1] for i in selection.split(',')]
        except (ValueError, IndexError): print("Ungültige Auswahl. Breche ab."); return
    else: selected_files = available_strategies
    strategies_data = {}
    print("\nLade Daten und Modelle für gewählte Strategien...")
    for filename in selected_files:
        with open(os.path.join(configs_dir, filename), 'r') as f: config = json.load(f)
        symbol, timeframe = config['market']['symbol'], config['market']['timeframe']
        safe_filename = f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"
        model_paths = {'model': os.path.join(models_dir, f'ann_predictor_{safe_filename}.h5'), 'scaler': os.path.join(models_dir, f'ann_scaler_{safe_filename}.joblib')}
        model, scaler = load_model_and_scaler(model_paths['model'], model_paths['scaler'])
        data = load_data(symbol, timeframe, start_date, end_date)
        if model and scaler and not data.empty:
            strategies_data[filename] = {'symbol': symbol, 'timeframe': timeframe, 'data': data, 'model': model, 'scaler': scaler, 'params': {**config.get('strategy', {}), **config.get('risk', {})}}
        else:
            print(f"WARNUNG: Konnte Daten/Modell für {filename} nicht laden. Wird ignoriert.")
    if not strategies_data:
        print("Konnte für keine der gewählten Strategien Daten laden. Breche ab."); return

    # HIER BEGINNEN DIE WICHTIGEN ÄNDERUNGEN

    equity_df = pd.DataFrame() # Initialisiere ein leeres DataFrame
    csv_path = ""
    caption = ""

    if is_auto:
        results = run_portfolio_optimizer(start_capital, strategies_data, start_date, end_date)
        if results and 'final_result' in results:
            final_report = results['final_result']
            print("\n======================================================="); print("     Ergebnis der automatischen Portfolio-Optimierung"); print("=======================================================")
            print(f"Zeitraum: {start_date} bis {end_date}\nStartkapital: {start_capital:.2f} USDT")
            print("\nOptimales Portfolio gefunden (" + str(len(results['optimal_portfolio'])) + " Strategien):")
            for strat_filename in results['optimal_portfolio']: print(f"  - {strat_filename}")
            print("\n--- Simulierte Performance dieses optimalen Portfolios ---")
            print(f"Endkapital:           {final_report['end_capital']:.2f} USDT"); print(f"Gesamt PnL:           {final_report['end_capital'] - start_capital:+.2f} USDT ({final_report['total_pnl_pct']:.2f}%)")
            print(f"Portfolio Max DD:     {final_report['max_drawdown_pct']:.2f}%")
            print(f"Liquidiert:           {'JA, am ' + final_report['liquidation_date'].strftime('%Y-%m-%d') if final_report['liquidation_date'] else 'NEIN'}")

            # KORREKTE SPEICHERLOGIK
            csv_path = os.path.join(PROJECT_ROOT, 'optimal_portfolio_equity.csv')
            caption = f"Automatischer Portfolio-Optimierungsbericht\nEndkapital: {final_report['end_capital']:.2f} USDT"
            equity_df = final_report.get('equity_curve')
    else:
        sim_data = {v['symbol']: v for k, v in strategies_data.items()}
        results = run_portfolio_simulation(start_capital, sim_data, start_date, end_date)
        if results:
            print("\n======================================================="); print("     Portfolio-Simulations-Ergebnis"); print("=======================================================")
            print(f"Zeitraum: {start_date} bis {end_date}\nStartkapital: {results['start_capital']:.2f} USDT")
            print("\n--- Gesamt-Performance ---")
            print(f"Endkapital:           {results['end_capital']:.2f} USDT"); print(f"Gesamt PnL:           {results['end_capital'] - results['start_capital']:+.2f} USDT ({results['total_pnl_pct']:.2f}%)")
            print(f"Anzahl Trades:        {results['trade_count']}"); print(f"Win-Rate:             {results['win_rate']:.2f}%")
            print(f"Portfolio Max DD:     {results['max_drawdown_pct']:.2f}% am {results['max_drawdown_date'].strftime('%Y-%m-%d') if results['max_drawdown_date'] else 'N/A'}")

            # KORREKTE SPEICHERLOGIK
            csv_path = os.path.join(PROJECT_ROOT, 'portfolio_equity_curve.csv')
            caption = f"Manueller Portfolio-Simulationsbericht\nEndkapital: {results['end_capital']:.2f} USDT"
            equity_df = results.get('equity_curve')

    # Zentraler Speicher- und Sende-Block
    if equity_df is not None and not equity_df.empty:
        print("\n--- Export ---")
        print(f"✔ Details zur Equity-Kurve wurden nach '{os.path.basename(csv_path)}' exportiert.")
        equity_df[['timestamp', 'equity', 'drawdown_pct']].to_csv(csv_path, index=False)
        print("=======================================================")

        try:
            with open(os.path.join(PROJECT_ROOT, 'secret.json'), 'r') as f: secrets = json.load(f)
            telegram_config = secrets.get('telegram', {})
            if telegram_config.get('bot_token'):
                print("Sende Bericht an Telegram...")
                send_document(telegram_config.get('bot_token'), telegram_config.get('chat_id'), csv_path, caption)
                print("✔ Bericht wurde erfolgreich an Telegram gesendet.")
        except Exception as e:
            print(f"ⓘ Konnte Bericht nicht an Telegram senden: {e}")
    else:
        print("\nKeine Equity-Daten zum Exportieren vorhanden.")


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
    else:
        run_single_analysis(start_date=start_date, end_date=end_date, start_capital=start_capital)
