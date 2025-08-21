# code/analysis/optimizer.py
import os, sys, json, pandas as pd, warnings
from itertools import product
import argparse

warnings.filterwarnings("ignore", category=FutureWarning)

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from analysis.backtest import load_data_for_timeframe, map_levels_to_ltf, run_jaeger_backtest
from utilities.strategy_logic import calculate_jaeger_signals, add_sma_to_htf

def run_jaeger_optimization(start_date, end_date, symbol, custom_leverage=None, timeframe_str=None, filter_mode=None, sma_periods_str=None, start_capital=None, initial_sls_str=None, tsl_lookbacks_str=None, target_pnl=None):
    if '/' not in symbol:
        formatted_symbol = f"{symbol.upper()}/USDT:USDT"
        print(f"Formatiere '{symbol}' zu '{formatted_symbol}'.")
    else: formatted_symbol = symbol.upper()

    config_path = os.path.join(os.path.dirname(__file__), '..', 'strategies', 'envelope', 'config.json')
    with open(config_path, 'r') as f: base_params = json.load(f)
    
    if custom_leverage is not None:
        print(f"HINWEIS: Standard-Hebel wird überschrieben. Verwende {custom_leverage}x für diesen Lauf.")
        base_params['leverage'] = custom_leverage
    if start_capital is not None:
        base_params['start_capital'] = start_capital

    base_params['symbol'] = formatted_symbol
    print(f"\n#################### START OPTIMIERUNG FÜR: {formatted_symbol} ####################")

    timeframe_pairs = [tuple(pair.split('/')) for pair in timeframe_str.split(',')]
    
    filter_options = []
    if filter_mode == 'on': filter_options = [True]
    elif filter_mode == 'off': filter_options = [False]
    else: filter_options = [True, False]

    sma_periods = [20] 
    if sma_periods_str:
        sma_periods = [int(p) for p in sma_periods_str.split()]
        
    initial_sl_list = [float(p) for p in initial_sls_str.split()]
    tsl_lookback_list = [int(p) for p in tsl_lookbacks_str.split()]

    param_grid = {
        'retest_tolerance_pct': [0.05, 0.1],
        'initial_sl_placement_pct': initial_sl_list,
        'tsl_lookback_candles': tsl_lookback_list,
        'use_sma_filter': filter_options,
        'sma_period': sma_periods
    }
    
    if filter_mode == 'off':
        param_grid['sma_period'] = [sma_periods[0]] 

    keys, values = zip(*param_grid.items())
    param_combinations = [dict(zip(keys, v)) for v in product(*values)]
    
    all_results = []
    
    for htf, ltf in timeframe_pairs:
        print(f"\n--- Teste Zeitrahmen-Paar: HTF={htf}, LTF={ltf} ---")
        print("Lade Daten für Optimierung...")
        htf_data_full = load_data_for_timeframe(formatted_symbol, htf, start_date, end_date)
        ltf_data_full = load_data_for_timeframe(formatted_symbol, ltf, start_date, end_date)
        if htf_data_full.empty or ltf_data_full.empty:
            print(f"Nicht genügend Daten für {formatted_symbol} mit Timeframes {htf}/{ltf}. Überspringe.")
            continue
        total_runs = len(param_combinations)
        print(f"Starte Optimierungslauf mit {total_runs} Kombinationen für {htf}/{ltf}...")
        for i, params_to_test in enumerate(param_combinations):
            print(f"\r  -> Bearbeite Variante {i+1} von {total_runs}...", end="", flush=True)
            current_params = base_params.copy()
            current_params.update(params_to_test)
            current_params['sma_filter']['enabled'] = params_to_test['use_sma_filter']
            current_params['sma_filter']['period'] = params_to_test['sma_period']
            current_params['htf_timeframe'], current_params['ltf_timeframe'] = htf, ltf
            htf_data = add_sma_to_htf(htf_data_full.copy(), current_params)
            ltf_with_levels = map_levels_to_ltf(ltf_data_full.copy(), htf_data, htf)
            data_with_signals = calculate_jaeger_signals(ltf_with_levels, None, current_params)
            result = run_jaeger_backtest(data_with_signals, current_params, verbose=False)
            all_results.append(result)
        print(f"\r  -> Bearbeite Variante {total_runs} von {total_runs}... Fertig.")

    if not all_results:
        print("\nKeine Ergebnisse erzielt."); return
        
    print("\n\n--- Optimierung über alle Zeitrahmen abgeschlossen ---")
    results_df = pd.DataFrame(all_results)
    params_df = pd.json_normalize(results_df['params'])
    results_df = pd.concat([results_df.drop('params', axis=1), params_df], axis=1)
    
    sorted_results_pnl = results_df.sort_values(
        by=['total_pnl_pct', 'win_rate', 'trades_count'], 
        ascending=[False, False, False]
    ).head(10)

    print(f"\nBeste Gesamtergebnisse für {formatted_symbol} (Top 10 nach PnL):")
    for i, row in sorted_results_pnl.reset_index(drop=True).iterrows():
        filter_status = f"Aktiviert (SMA {int(row['sma_filter.period'])})" if row['sma_filter.enabled'] else "Deaktiviert"
        print("\n" + "="*30)
        print(f"     --- PLATZ {i+1} ---")
        print("="*30)
        print("\n  LEISTUNG:")
        print(f"    Gewinn (PnL):        {row['total_pnl_pct']:.2f} % (Hebel: {row['leverage']}x)")
        if 'end_capital' in row and pd.notna(row['end_capital']):
            print(f"    Endkapital:          {row['end_capital']:.2f} USDT (Start: {row['start_capital']:.2f} USDT)")
        print(f"    Trefferquote:        {row['win_rate']:.2f} %")
        print(f"    Anzahl Trades:       {int(row['trades_count'])}")
        print("\n  BESTE EINSTELLUNGEN:")
        print(f"    Zeitrahmen:          {row['htf_timeframe']} / {row['ltf_timeframe']}")
        print(f"    SMA Filter:          {filter_status}")
        print(f"    Retest Toleranz:     {row['retest_tolerance_pct']}%")
        print(f"    Initial SL Platz.:   {row['initial_sl_placement_pct']}%")
        print(f"    TSL Lookback:        {int(row['tsl_lookback_candles'])} Kerzen")
    
    if target_pnl is not None:
        results_df['pnl_diff'] = abs(results_df['total_pnl_pct'] - target_pnl)
        sorted_results_target = results_df.sort_values(by='pnl_diff').head(1)
        
        print(f"\nBeste Übereinstimmung für Ziel-PnL von {target_pnl}%:")
        if not sorted_results_target.empty:
            row = sorted_results_target.iloc[0]
            filter_status = f"Aktiviert (SMA {int(row['sma_filter.period'])})" if row['sma_filter.enabled'] else "Deaktiviert"
            print("\n" + "="*30)
            print(f"     --- BESTE ÜBEREINSTIMMUNG ---")
            print("="*30)
            print("\n  LEISTUNG:")
            print(f"    Gewinn (PnL):        {row['total_pnl_pct']:.2f} % (Hebel: {row['leverage']}x)")
            if 'end_capital' in row and pd.notna(row['end_capital']):
                print(f"    Endkapital:          {row['end_capital']:.2f} USDT (Start: {row['start_capital']:.2f} USDT)")
            print(f"    Trefferquote:        {row['win_rate']:.2f} %")
            print(f"    Anzahl Trades:       {int(row['trades_count'])}")
            print("\n  GEFUNDENE PARAMETER:")
            print(f"    Zeitrahmen:          {row['htf_timeframe']} / {row['ltf_timeframe']}")
            print(f"    SMA Filter:          {filter_status}")
            print(f"    Retest Toleranz:     {row['retest_tolerance_pct']}%")
            print(f"    Initial SL Platz.:   {row['initial_sl_placement_pct']}%")
            print(f"    TSL Lookback:        {int(row['tsl_lookback_candles'])} Kerzen")
    
    print("\n" + "="*30)
    print(f"#################### ENDE OPTIMIERUNG FÜR: {formatted_symbol} ####################\n")
    if not sorted_results_pnl.empty:
        best_run = sorted_results_pnl.iloc[0]
        filter_status_short = f"ON (SMA{int(best_run['sma_filter.period'])})" if best_run['sma_filter.enabled'] else "OFF"
        end_capital_str = f"{best_run['end_capital']:.2f}" if 'end_capital' in best_run and pd.notna(best_run['end_capital']) else "N/A"
        print(f"BEST_RESULT_FOR_SCRIPT;{best_run['total_pnl_pct']};{best_run['win_rate']};{best_run['trades_count']};{best_run['symbol']};{best_run['htf_timeframe']}/{best_run['ltf_timeframe']};{filter_status_short};{best_run['retest_tolerance_pct']};{best_run['initial_sl_placement_pct']};{best_run['tsl_lookback_candles']};{best_run['leverage']};{end_capital_str}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Strategie-Optimierer für den Jäger Bot.")
    parser.add_argument('--start', required=True, help="Startdatum YYYY-MM-DD")
    parser.add_argument('--end', required=True, help="Enddatum YYYY-MM-DD")
    parser.add_argument('--symbol', required=True, help="Handelspaar (z.B. BTC)")
    parser.add_argument('--leverage', type=float, help="Optional: Hebel")
    parser.add_argument('--start_capital', type=float, help="Optional: Startkapital in USDT")
    parser.add_argument('--timeframes', required=True, help="Zu testende Zeitrahmen-Paare")
    parser.add_argument('--filter_mode', required=True, help="SMA Filter Modus: 'on', 'off', oder 'both'")
    parser.add_argument('--sma_periods', help="Zu testende SMA Perioden, z.B. '20 50'")
    parser.add_argument('--initial_sls', required=True, help="Zu testende Initial SL Werte")
    parser.add_argument('--tsl_lookbacks', required=True, help="Zu testende TSL Lookback Werte")
    parser.add_argument('--target_pnl', type=float, help="Optional: Ziel-PnL in % für die Zielsuche")
    args = parser.parse_args()
    run_jaeger_optimization(args.start, args.end, args.symbol, args.leverage, args.timeframes, args.filter_mode, args.sma_periods, args.start_capital, args.initial_sls, args.tsl_lookbacks, args.target_pnl)
