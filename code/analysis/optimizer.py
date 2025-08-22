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

def run_titan_optimization(start_date, end_date, symbols, leverage, start_capital, trade_size_pct):
    print("\n=================================================")
    print("           TITANBOT - STRATEGIE-OPTIMIZER          ")
    print("=================================================")
    print("Wähle eine Strategie zur Optimierung:")
    strategy_names = list(STRATEGY_CONFIG.keys())
    for i, name in enumerate(strategy_names):
        print(f"  [{i+1}] {name.replace('_', ' ').title()}")
    print(f"  [{len(strategy_names) + 1}] Alle Strategien nacheinander testen")

    try:
        choice = int(input(f"Auswahl [1-{len(strategy_names) + 1}]: "))
    except ValueError:
        print("Ungültige Eingabe. Abbruch."); return

    strategies_to_run = []
    if choice == len(strategy_names) + 1:
        strategies_to_run = strategy_names
        print("\nINFO: Alle Strategien werden mit Standard-Parametern getestet.")
    elif 1 <= choice <= len(strategy_names):
        strategies_to_run.append(strategy_names[choice - 1])
    else:
        print("Ungültige Auswahl. Abbruch."); return

    timeframe_input = input("Zu testende Timeframe(s) eingeben (z.B. 15m 1h 4h): ")
    if not timeframe_input:
        print("Fehler: Mindestens ein Timeframe ist erforderlich. Abbruch."); return
    timeframes_to_run = timeframe_input.split()
        
    grand_total_results = []

    for symbol_short in symbols:
        if '/' not in symbol_short:
            symbol = f"{symbol_short.upper()}/USDT:USDT"
            print(f"\nINFO: Symbol '{symbol_short}' wird als '{symbol}' verarbeitet.")
        else:
            symbol = symbol_short.upper()

        for timeframe in timeframes_to_run:
            print("\n" + "="*60)
            print(f"=== OPTIMIERE: {symbol} auf TIMEFRAME: {timeframe.upper()} ===")
            print("="*60)

            print("\nLade historische Daten...")
            data = load_data(symbol, timeframe, start_date, end_date)
            if data.empty:
                print(f"Nicht genügend Daten. Überspringe {symbol} auf {timeframe}.")
                continue

            for strategy_name in strategies_to_run:
                print("\n" + "#"*60)
                print(f"#####  Strategie: {strategy_name.upper()}  #####")
                print("#"*60)

                if len(strategies_to_run) > 1:
                    param_grid = parse_default_params(strategy_name)
                else:
                    param_grid = get_user_params(strategy_name)
                
                signal_func = STRATEGY_CONFIG[strategy_name]['signal_func']
                keys, values = zip(*param_grid.items())
                param_combinations = [dict(zip(keys, v)) for v in product(*values)]
                total_runs = len(param_combinations)

                proceed = True
                estimated_total_seconds = 0
                if total_runs > 5:
                    print("\nFühre Benchmark zur Zeitabschätzung durch...", end="", flush=True)
                    sample_size = min(5, total_runs)
                    sample_params = param_combinations[:sample_size]
                    start_benchmark = time.time()
                    for params_to_test in sample_params:
                        base_params = {'leverage': leverage, 'start_capital': start_capital, 'trade_size_pct': trade_size_pct}
                        current_params = {**base_params, **params_to_test}
                        data_with_signals = signal_func(data.copy(), params_to_test)
                        run_titan_backtest(data_with_signals, current_params, verbose=False)
                    end_benchmark = time.time()
                    avg_time_per_variant = (end_benchmark - start_benchmark) / sample_size
                    estimated_total_seconds = avg_time_per_variant * total_runs
                    print(" Fertig.")

                print(f"\nEs werden insgesamt {total_runs} Varianten simuliert.")
                if estimated_total_seconds > 0:
                    minutes = int(estimated_total_seconds / 60)
                    seconds = int(estimated_total_seconds % 60)
                    if estimated_total_seconds > 60:
                        print(f"Geschätzte Gesamtdauer: ca. {minutes} Minuten und {seconds} Sekunden.")
                    else:
                        print(f"Geschätzte Gesamtdauer: ca. {int(estimated_total_seconds)} Sekunden.")
                
                if estimated_total_seconds > 120:
                    confirm = input("\nMöchten Sie mit der Berechnung fortfahren? [j/N]: ")
                    if confirm.lower() != 'j': proceed = False
                elif total_runs > 5:
                     print("Berechnung startet automatisch (geschätzte Dauer unter 2 Minuten).")
                
                if not proceed:
                    print("Optimierung für diese Strategie abgebrochen.")
                    continue

                print(f"\nStarte Lauf mit {total_runs} Kombinationen...")

                all_results_for_run = []
                for i, params_to_test in enumerate(param_combinations):
                    print(f"\r  -> Simuliere Variante {i+1}/{total_runs}...", end="", flush=True)
                    base_params = {'strategy_name': strategy_name, 'symbol': symbol, 'timeframe': timeframe, 'leverage': leverage, 'start_capital': start_capital, 'trade_size_pct': trade_size_pct}
                    current_params = {**base_params, **params_to_test}
                    data_with_signals = signal_func(data.copy(), params_to_test)
                    result = run_titan_backtest(data_with_signals, current_params, verbose=False)
                    all_results_for_run.append(result)
                print(" Fertig.")

                if not all_results_for_run:
                    print(f"\nKeine Ergebnisse für {strategy_name} erzielt.")
                    continue
                
                grand_total_results.extend(all_results_for_run)
                
                # --- ZWISCHENERGEBNISSE WURDEN HIER ENTFERNT ---

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
    print("\n" + "="*40)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Strategie-Optimierer für den Titan Bot.")
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--symbol', required=True, help="Ein oder mehrere Handelspaare (z.B. 'BTC' oder 'BTC ETH SOL')")
    parser.add_argument('--leverage', type=float, default=1.0, help="Hebel für die Simulation. Für Kapital-Extrapolation 1.0 verwenden.")
    parser.add_argument('--start_capital', type=float, default=1000.0)
    parser.add_argument('--trade_size_pct', type=float, default=10.0)
    args = parser.parse_args()

    symbols_to_run = args.symbol.split()
    
    run_titan_optimization(
        args.start, 
        args.end, 
        symbols_to_run,
        args.leverage, 
        args.start_capital,
        args.trade_size_pct
    )
