# code/analysis/run_backtest.py

import json
import os
import sys
import pandas as pd

# Pfade und Module laden
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from analysis.backtest import load_data, run_envelope_backtest
from utilities.strategy_logic import calculate_envelope_indicators

def main():
    print("\n--- [Modus: Einzel-Backtest] ---")
    
    # Lade die aktuelle Live-Konfiguration
    try:
        project_root = os.path.join(os.path.dirname(__file__), '..', '..')
        config_path = os.path.join(project_root, 'code', 'strategies', 'envelope', 'config.json')
        with open(config_path, 'r') as f:
            config = json.load(f)
        print(f"Lade Live-Konfiguration für {config['market']['symbol']} ({config['market']['timeframe']})...")
    except Exception as e:
        print(f"Fehler beim Laden der config.json: {e}")
        return

    # Frage den Backtest-Zeitraum ab
    start_date = input("Startdatum für den Backtest eingeben (JJJJ-MM-TT): ")
    end_date = input("Enddatum für den Backtest eingeben (JJJJ-MM-TT): ")
    start_capital = float(input("Startkapital für den Backtest eingeben (z.B. 1000): "))

    symbol = config['market']['symbol']
    timeframe = config['market']['timeframe']

    # Lade die historischen Daten
    data = load_data(symbol, timeframe, start_date, end_date)
    if data.empty:
        print(f"Keine Daten für den Zeitraum {start_date} bis {end_date} gefunden.")
        return

    # Kombiniere die Parameter für die Indikatoren- und Backtest-Funktion
    params = {
        **config['strategy'],
        **config['risk'],
        'start_capital': start_capital
    }

    # Führe den Backtest durch
    print("Berechne Indikatoren und führe Backtest aus...")
    data_with_indicators = calculate_envelope_indicators(data.copy(), params)
    result = run_envelope_backtest(data_with_indicators.dropna(), params)

    # Gib die Ergebnisse aus
    print("\n" + "="*50)
    print("    +++ BACKTEST-ERGEBNIS +++")
    print("="*50)
    print(f"  Zeitraum:           {start_date} bis {end_date}")
    print(f"  Startkapital:       {start_capital:.2f} USDT")
    print(f"  Endkapital:         {result['end_capital']:.2f} USDT")
    print(f"  Gesamtgewinn (PnL): {result['total_pnl_pct']:.2f} %")
    print(f"  Max. Drawdown:      {result['max_drawdown_pct']*100:.2f} %")
    print(f"  Anzahl Trades:      {result['trades_count']}")
    print(f"  Win-Rate:           {result['win_rate']:.2f} %")
    print("="*50)

if __name__ == "__main__":
    main()
