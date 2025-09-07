# code/analysis/run_backtest.py

import json
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from analysis.backtest import load_data, run_smc_backtest

def main():
    print("\n--- [Modus: Einzel-Backtest (TitanBot)] ---")
    try:
        project_root = os.path.join(os.path.dirname(__file__), '..', '..')
        config_path = os.path.join(project_root, 'code', 'strategies', 'envelope', 'config.json')
        with open(config_path, 'r') as f: config = json.load(f)
        print(f"Lade Live-Konfiguration für {config['market']['symbol']} ({config['market']['timeframe']})...")
    except Exception as e:
        print(f"Fehler beim Laden der config.json: {e}"); return

    start_date = input("Startdatum für den Backtest eingeben (JJJJ-MM-TT): ")
    end_date = input("Enddatum für den Backtest eingeben (JJJJ-MM-TT): ")
    start_capital = float(input("Startkapital für den Backtest eingeben (z.B. 1000): "))

    data = load_data(config['market']['symbol'], config['market']['timeframe'], start_date, end_date)
    if data.empty: print(f"Keine Daten für den Zeitraum gefunden."); return

    params = {**config['strategy'], **config['risk'], 'start_capital': start_capital}
    print("Führe TitanBot (SMC) Backtest aus...")
    result = run_smc_backtest(data.copy(), params)

    print("\n" + "="*50 + "\n    +++ BACKTEST-ERGEBNIS +++\n" + "="*50)
    print(f"  Zeitraum:           {start_date} bis {end_date}")
    print(f"  Startkapital:       {start_capital:.2f} USDT")
    print(f"  Endkapital:         {result['end_capital']:.2f} USDT")
    print(f"  Gesamtgewinn (PnL): {result['total_pnl_pct']:.2f} %")
    print(f"  Max. Drawdown:      {result['max_drawdown_pct']*100:.2f} %")
    print(f"  Anzahl Trades:      {result['trades_count']}")
    print(f"  Win-Rate:           {result['win_rate']:.2f} %")
    print("="*50)

    trade_log_list = result.get('trade_log', [])
    if trade_log_list:
        print("\n  HANDELS-CHRONIK (ERSTE 10 UND LETZTE 10 TRADES):")
        display_list = trade_log_list[:10] + ([None] + trade_log_list[-10:] if len(trade_log_list) > 20 else [])
        print("  " + "-"*95)
        print("  {:^28} | {:<7} | {:<13} | {:>17} | {:>18}".format("Datum & Uhrzeit (UTC)", "Seite", "Grund", "Gewinn je Trade", "Neuer Kontostand"))
        print("  " + "-"*95)
        for trade in display_list:
            if trade is None: print("  ...".center(97)); continue
            print(f"  {trade['timestamp']:<28} | {trade['side'].capitalize():<7} | {trade['reason']:<13} | {f'{trade['pnl']:+.2f} USDT':>17} | {f'{trade['balance']:.2f} USDT':>18}")
        print("  " + "-"*95)

if __name__ == "__main__":
    main()
