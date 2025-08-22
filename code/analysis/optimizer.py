import os
import sys
import json
import pandas as pd
import warnings
from itertools import product
import argparse
import time

warnings.filterwarnings("ignore", category=FutureWarning)

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from analysis.backtest import load_data, run_titan_backtest
from utilities.strategy_logic import calculate_momentum_signals, calculate_volatility_signals, calculate_tidal_wave_signals

STRATEGY_CONFIG = {
    "momentum_accelerator": {"signal_func": calculate_momentum_signals, "params": {"volume_ma_period": "20 30", "volume_ma_multiplier": "1.5 2.0", "crv": "1.5 2.0 2.5"}},
    "volatility_catcher": {"signal_func": calculate_volatility_signals, "params": {"bb_period": "20 25", "bb_std_dev": "2 2.5"}},
    "tidal_wave_rider": {"signal_func": calculate_tidal_wave_signals, "params": {"ema_fast_period": "9 12", "ema_slow_period": "21 26"}}
}
# ... (Funktionen get_user_params, parse_default_params bleiben unverändert) ...

def run_titan_optimization(start_date, end_date, symbols, leverage, start_capital, trade_size_pct):
    # ... (Anfang der Funktion und alle Schleifen bleiben unverändert) ...
    # ... (Die gesamte Berechnungslogik bleibt unverändert) ...
    
    # FINALE GESAMTAUSWERTUNG am Ende
    if not grand_total_results:
        print("\nKeine Ergebnisse für eine Gesamtauswertung vorhanden."); return

    print("\n" + "#"*70)
    print("##########      FINALE GESAMTAUSWERTUNG (TOP 10 ALLER LÄUFE)     ##########")
    print("#"*70)

    final_df = pd.DataFrame(grand_total_results)
    params_df = pd.json_normalize(final_df['params'])
    final_df = pd.concat([final_df.drop('params', axis=1), params_df], axis=1)
    overall_best = final_df.sort_values(by='total_pnl_pct', ascending=False).head(10)

    for i, row in overall_best.reset_index(drop=True).iterrows():
        print("\n" + "="*40)
        print(f"            --- GLOBALER PLATZ {i+1} ---")
        print("="*40)
        
        strategy_name_for_row = row['strategy_name']
        print(f"  HANDELSPAAR: {row['symbol']}")
        print(f"  TIMEFRAME:   {row['timeframe']}")
        print(f"  STRATEGIE:   {strategy_name_for_row.replace('_', ' ').title()}")
        
        print("\n  LEISTUNG:")
        print(f"    Gewinn (PnL):       {row['total_pnl_pct']:.2f} % (bei {row['leverage']:.0f}x Hebel)")
        print(f"    Endkapital (bei {row['leverage']:.0f}x):{row['end_capital']:.2f} USDT")
        print(f"    Anzahl Trades:      {int(row['trades_count'])}")
        if row.get('end_capital_max_lev', 0) > 0 and row['leverage'] == 1.0:
            print(f"    Maximaler Hebel:    {row['max_leverage']:.2f}x (-> {row['end_capital_max_lev']:.2f} USDT)")
            print(f"    Empfohlener Hebel:  {row['recommended_leverage']:.2f}x (-> {row['end_capital_rec_lev']:.2f} USDT)")
        else:
            print(f"    Maximaler Hebel:    {row.get('max_leverage', float('inf')):.2f}x")
            print(f"    Empfohlener Hebel:  {row['recommended_leverage']:.2f}x")
        
        param_keys_for_strategy = list(STRATEGY_CONFIG[strategy_name_for_row]['params'].keys())
        print("\n  BESTE PARAMETER:")
        for p_name in param_keys_for_strategy:
            print(f"    {p_name:<20}{row[p_name]}")
        
        # +++ NEU: Anzeige der Handels-Chronik bei < 30 Trades +++
        if int(row['trades_count']) < 30 and 'trade_dates' in row and not pd.isna(row['trade_dates']):
            try:
                # Die Daten kommen als String-Repräsentation einer Liste an, wir müssen sie evaluieren.
                import ast
                trade_dates_list = ast.literal_eval(row['trade_dates'])
                if trade_dates_list:
                    print("\n  HANDELS-CHRONIK (GERINGE ANZAHL):")
                    dates = pd.to_datetime(trade_dates_list)
                    date_strings = [d.strftime('%Y-%m-%d') for d in dates]
                    # Gruppiere Daten in Zeilen zu je 5, um die Ausgabe übersichtlich zu halten
                    for j in range(0, len(date_strings), 5):
                        print(f"    {', '.join(date_strings[j:j+5])}")
            except Exception as e:
                # Fallback, falls etwas bei der Konvertierung schiefgeht
                print(f"\n  HANDELS-CHRONIK: Konnte Daten nicht anzeigen. Fehler: {e}")

    print("\n" + "="*40)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Strategie-Optimierer für den Titan Bot.")
    # ... (Rest des Skripts bleibt unverändert)
