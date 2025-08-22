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
    print(f"\n--- Parameter für '{strategy_name}' konfigurieren ---")
    config = STRATEGY_CONFIG[strategy_name]['params']
    user_params = {}
    for param, default_values in config.items():
        user_input = input(f"Werte für '{param}' eingeben (Enter für '{default_values}'): ")
        user_params[param] = user_input.split() if user_input else default_values.split()
        try:
            user_params[param] = [int(p) for p in user_params[param]]
        except ValueError:
            user_params[param] = [float(p) for p in user_params[param]]
    return user_params

def parse_default_params(strategy_name):
    config = STRATEGY_CONFIG[strategy_name]['params']
    parsed_params = {}
    for param, values_str in config.items():
        values = values_str.split()
        try:
            parsed_params[param] = [int(p) for p in values]
        except ValueError:
            parsed_params[param] = [float(p) for p in values]
    return parsed_params

def run_titan_optimization(start_date, end_date, symbols, leverage, start_capital, trade_size_pct, log_threshold):
    print("\n=================================================")
    print("           TITANBOT - STRATEGIE-OPTIMIZER          ")
    # ... (Anfang der Funktion bleibt unverändert) ...
    
    # FINALE GESAMTAUSWERTUNG
    if not grand_total_results:
        print("\nKeine Ergebnisse für eine Gesamtauswertung vorhanden."); return

    print("\n" + "#"*70)
    print("##########      FINALE GESAMTAUSWERTUNG (TOP 10 ALLER LÄUFE)     ##########")
    print("#"*70)
    final_df = pd.DataFrame(grand_total_results)
    final_df['trade_log'] = final_df['trade_log'].apply(lambda x: json.dumps(x))
    params_df = pd.json_normalize(final_df['params'])
    final_df = pd.concat([final_df.drop('params', axis=1), params_df], axis=1)
    overall_best = final_df.sort_values(by='total_pnl_pct', ascending=False).head(10)

    for i, row in overall_best.reset_index(drop=True).iterrows():
        print("\n" + "="*40); print(f"            --- GLOBALER PLATZ {i+1} ---"); print("="*40)
        strategy_name_for_row = row['strategy_name']
        print(f"  HANDELSPAAR: {row['symbol']}"); print(f"  TIMEFRAME:   {row['timeframe']}"); print(f"  STRATEGIE:   {strategy_name_for_row.replace('_', ' ').title()}")
        print("\n  LEISTUNG:")
        print(f"    Gewinn (PnL):       {row['total_pnl_pct']:.2f} % (bei {row['leverage']:.0f}x Hebel)")
        print(f"    Endkapital (bei {row['leverage']:.0f}x):{row['end_capital']:.2f} USDT")
        print(f"    Anzahl Trades:      {int(row['trades_count'])}")
        # +++ HIER IST DIE GEÄNDERTE AUSGABE +++
        print(f"    Max. Portfolio-Hebel: {row.get('max_portfolio_leverage', 0):.2f}x")
        if row['recommended_leverage'] == 0.0:
            print(f"    Empfohlener Hebel:  {row['recommended_leverage']:.2f}x (Strategie nicht profitabel)")
        else:
            print(f"    Empfohlener Hebel:  {row['recommended_leverage']:.2f}x")
        
        param_keys_for_strategy = list(STRATEGY_CONFIG[strategy_name_for_row]['params'].keys())
        print("\n  BESTE PARAMETER:")
        for p_name in param_keys_for_strategy:
            print(f"    {p_name:<20}{row[p_name]}")
        
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
    parser.add_argument('--log_threshold', type=int, default=30)
    args = parser.parse_args()
    symbols_to_run = args.symbol.split()
    run_titan_optimization(args.start, args.end, symbols_to_run, args.leverage, args.start_capital, args.trade_size_pct, args.log_threshold)
