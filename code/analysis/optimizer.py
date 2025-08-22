import os
import sys
import json
import pandas as pd
import warnings
from itertools import product
import argparse
import time
import ast

warnings.filterwarnings("ignore", category=FutureWarning)

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from analysis.backtest import load_data, run_titan_backtest
from utilities.strategy_logic import calculate_momentum_signals, calculate_volatility_signals, calculate_tidal_wave_signals

STRATEGY_CONFIG = {
    "momentum_accelerator": {"signal_func": calculate_momentum_signals, "params": {"volume_ma_period": "20 30", "volume_ma_multiplier": "1.5 2.0", "crv": "1.5 2.0 2.5"}},
    "volatility_catcher": {"signal_func": calculate_volatility_signals, "params": {"bb_period": "20 25", "bb_std_dev": "2 2.5"}},
    "tidal_wave_rider": {"signal_func": calculate_tidal_wave_signals, "params": {"ema_fast_period": "9 12", "ema_slow_period": "21 26"}}
}

def get_user_params(strategy_name):
    # ... (Diese Funktion bleibt unverändert)
    pass

def parse_default_params(strategy_name):
    # ... (Diese Funktion bleibt unverändert)
    pass

def run_titan_optimization(start_date, end_date, symbols, leverage, start_capital, trade_size_pct, log_threshold): # +++ NEUER PARAMETER
    # ... (Anfang der Funktion bleibt unverändert) ...
    
    # FINALE GESAMTAUSWERTUNG am Ende
    # ...
    for i, row in overall_best.reset_index(drop=True).iterrows():
        # ... (Anfang der Schleife bleibt unverändert) ...
        
        # +++ NEU: Flexibler Schwellenwert wird hier verwendet +++
        if int(row['trades_count']) < log_threshold and 'trade_log' in row and not pd.isna(row['trade_log']):
            try:
                trade_log_list = json.loads(row['trade_log'])
                if trade_log_list:
                    print("\n  DETAILLIERTE HANDELS-CHRONIK (GERINGE ANZAHL):")
                    print("    Datum        | Seite  | Einstieg | Ausstieg | Gewinn (USDT) | Kontostand")
                    print("    -------------------------------------------------------------------------")
                    for trade in trade_log_list:
                        side_str = trade['side'].capitalize().ljust(5)
                        entry_str = f"{trade['entry']:.2f}".ljust(8)
                        exit_str = f"{trade['exit']:.2f}".ljust(8)
                        pnl_str = f"{trade['pnl']:+9.2f}".ljust(13)
                        balance_str = f"{trade['balance']:.2f} USDT"
                        print(f"    {trade['date']} | {side_str} | {entry_str} | {exit_str} | {pnl_str} | {balance_str}")
            except Exception:
                pass
    print("\n" + "="*40)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Strategie-Optimierer für den Titan Bot.")
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--symbol', required=True)
    parser.add_argument('--leverage', type=float, default=1.0)
    parser.add_argument('--start_capital', type=float, default=1000.0)
    parser.add_argument('--trade_size_pct', type=float, default=10.0)
    # +++ NEUER PARAMETER WIRD HIER DEFINIERT +++
    parser.add_argument('--log_threshold', type=int, default=30, help="Max. Anzahl Trades für die Anzeige des Detail-Logs.")
    args = parser.parse_args()

    symbols_to_run = args.symbol.split()
    
    run_titan_optimization(
        args.start, 
        args.end, 
        symbols_to_run,
        args.leverage, 
        args.start_capital,
        args.trade_size_pct,
        args.log_threshold # +++ NEUER PARAMETER WIRD HIER ÜBERGEBEN
    )
