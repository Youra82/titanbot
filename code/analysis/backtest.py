# code/analysis/backtest.py
import os, sys, json, pandas as pd, argparse, warnings
from datetime import timedelta

# Unterdrückt störende Warnungen für eine saubere Ausgabe
warnings.filterwarnings("ignore", category=FutureWarning)

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from utilities.strategy_logic import get_daily_levels, calculate_jaeger_signals, add_sma_to_htf

def load_data_for_timeframe(symbol, timeframe, start_date_str, end_date_str):
    cache_dir = os.path.join(os.path.dirname(__file__), 'historical_data')
    os.makedirs(cache_dir, exist_ok=True)
    symbol_filename = symbol.replace('/', '-').replace(':', '-')
    cache_file = os.path.join(cache_dir, f"{symbol_filename}_{timeframe}.csv")
    if os.path.exists(cache_file):
        print(f"Lade Daten für {timeframe} aus dem Cache...")
        data = pd.read_csv(cache_file, index_col='timestamp', parse_dates=True)
        data.index = pd.to_datetime(data.index, utc=True)
        required_start = pd.to_datetime(start_date_str, utc=True)
        if data.index.min() <= required_start and data.index.max() >= pd.to_datetime(end_date_str, utc=True):
             print("Cache-Daten sind ausreichend."); return data.loc[start_date_str:end_date_str]
    print(f"Cache für {timeframe} nicht ausreichend. Lade neue Daten...")
    try:
        from utilities.bitget_futures import BitgetFutures
        project_root = os.path.join(os.path.dirname(__file__), '..', '..')
        key_path = os.path.abspath(os.path.join(project_root, 'secret.json'))
        with open(key_path, "r") as f: api_setup = json.load(f)['envelope']
        bitget = BitgetFutures(api_setup)
        download_start = (pd.to_datetime(start_date_str) - timedelta(days=50)).strftime('%Y-%m-%d')
        download_end = (pd.to_datetime(end_date_str) + timedelta(days=1)).strftime('%Y-%m-%d')
        print(f"Anfrage für {timeframe} von {download_start} bis {download_end}...")
        full_data = bitget.fetch_historical_ohlcv(symbol, timeframe, download_start, download_end)
        if full_data is not None and not full_data.empty:
            full_data.to_csv(cache_file)
            print(f"Cache für {timeframe} erfolgreich aktualisiert.")
            return full_data.loc[start_date_str:end_date_str]
        else:
            print(f"--> FEHLER: Keine historischen Daten für {timeframe} erhalten."); return pd.DataFrame()
    except Exception as e:
        print(f"Fehler beim Herunterladen der Daten für {timeframe}: {e}"); return pd.DataFrame()

def map_levels_to_ltf(ltf_df, htf_df, htf_timeframe):
    ltf_df = ltf_df.copy()
    htf_period = pd.tseries.frequencies.to_offset(htf_timeframe)
    for i in range(1, len(htf_df)):
        htf_prev_candle = htf_df.iloc[i-1]
        htf_current_candle = htf_df.iloc[i]
        levels = {
            'htf_timestamp': htf_prev_candle.name, 'htf_wick_high': htf_prev_candle.high,
            'htf_wick_low': htf_prev_candle.low, 'htf_body_top': max(htf_prev_candle.open, htf_prev_candle.close),
            'htf_body_bottom': min(htf_prev_candle.open, htf_prev_candle.close), 'htf_sma_trend': htf_prev_candle.get('sma_trend', 0)
        }
        mask = (ltf_df.index >= htf_current_candle.name) & (ltf_df.index < htf_current_candle.name + htf_period)
        for col, val in levels.items(): ltf_df.loc[mask, col] = val
    ltf_df.dropna(subset=['htf_timestamp'], inplace=True)
    return ltf_df

def run_jaeger_backtest(data, params, verbose=True):
    if verbose: print("\nFühre Jäger-Backtest aus...")
    leverage = params.get('leverage', 1.0); fee_pct = 0.05 / 100
    status, side, entry_price, initial_sl_price, runner_sl_price, tp1_price = 'none', None, 0.0, 0.0, 0.0, 0.0
    liquidation_price = 0.0
    
    start_capital = params.get('start_capital', None)
    current_capital = start_capital if start_capital is not None else 0
    trade_size_pct_of_capital = params.get('trade_size_pct', 30) / 100
    
    max_adverse_excursion = 0.0
    current_trade_mae = 0.0
    total_pnl_pct_of_portfolio = 0.0
    trades_count = 0
    wins_count = 0
    
    for i in range(1, len(data)):
        current_candle = data.iloc[i]
        if status in ['part1', 'part2']:
            adverse_move = 0.0
            if side == 'long': adverse_move = (entry_price - current_candle['low']) / entry_price
            else: adverse_move = (current_candle['high'] - entry_price) / entry_price
            current_trade_mae = max(current_trade_mae, adverse_move)
            liquidated = False
            if side == 'long' and current_candle['low'] <= liquidation_price: liquidated = True
            elif side == 'short' and current_candle['high'] >= liquidation_price: liquidated = True
            
            if liquidated:
                pnl_pct = -1.0 
                if start_capital: current_capital += current_capital * trade_size_pct_of_capital * pnl_pct
                total_pnl_pct_of_portfolio += trade_size_pct_of_capital * pnl_pct
                if verbose: print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | LIQUIDATION | PnL: -100.00% der Margin")
                max_adverse_excursion = max(max_adverse_excursion, current_trade_mae)
                status = 'none'; continue

            pnl_pct_of_position = 0
            trade_closed = False
            
            if status == 'part2':
                tsl_lookback = params.get('tsl_lookback_candles', 2)
                relevant_candles = data.iloc[max(0, i-tsl_lookback):i]
                if side == 'long':
                    new_tsl_price = relevant_candles['low'].min()
                    if new_tsl_price > runner_sl_price: runner_sl_price = new_tsl_price
                else:
                    new_tsl_price = relevant_candles['high'].max()
                    if new_tsl_price < runner_sl_price: runner_sl_price = new_tsl_price
                if (side == 'long' and current_candle['low'] <= runner_sl_price) or (side == 'short' and current_candle['high'] >= runner_sl_price):
                    exit_price = runner_sl_price
                    pnl_pct_of_position = ((exit_price - entry_price) / entry_price if side == 'long' else (entry_price - exit_price) / entry_price)
                    if start_capital: current_capital += current_capital * trade_size_pct_of_capital * (pnl_pct_of_position * leverage * 0.5 - fee_pct * leverage)
                    total_pnl_pct_of_portfolio += trade_size_pct_of_capital * (pnl_pct_of_position * leverage * 0.5 - fee_pct * leverage)
                    if pnl_pct_of_position > 0: wins_count += 0.5
                    if verbose: print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | RUNNER STOP | PnL: {pnl_pct_of_position*100*leverage*0.5:.2f}%")
                    trade_closed = True
            
            if status == 'part1':
                if (side == 'long' and current_candle['low'] <= initial_sl_price) or (side == 'short' and current_candle['high'] >= initial_sl_price):
                    exit_price = initial_sl_price
                    pnl_pct_of_position = ((exit_price - entry_price) / entry_price if side == 'long' else (entry_price - exit_price) / entry_price)
                    if start_capital: current_capital += current_capital * trade_size_pct_of_capital * (pnl_pct_of_position * leverage - 2 * fee_pct * leverage)
                    total_pnl_pct_of_portfolio += trade_size_pct_of_capital * (pnl_pct_of_position * leverage - 2 * fee_pct * leverage)
                    if verbose: print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | STOP-LOSS   | PnL: {pnl_pct_of_position*100*leverage:.2f}%")
                    trade_closed = True
                
                elif (side == 'long' and current_candle['high'] >= tp1_price) or (side == 'short' and current_candle['low'] <= tp1_price):
                    exit_price = tp1_price
                    pnl_pct_of_position = ((exit_price - entry_price) / entry_price if side == 'long' else (entry_price - exit_price) / entry_price)
                    if start_capital: current_capital += current_capital * trade_size_pct_of_capital * (pnl_pct_of_position * leverage * 0.5 - fee_pct * leverage)
                    total_pnl_pct_of_portfolio += trade_size_pct_of_capital * (pnl_pct_of_position * leverage * 0.5 - fee_pct * leverage)
                    wins_count += 0.5
                    if verbose: print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | TP1 REACHED | PnL: {pnl_pct_of_position*100*leverage*0.5:.2f}%")
                    status, runner_sl_price = 'part2', entry_price
            
            if trade_closed:
                max_adverse_excursion = max(max_adverse_excursion, current_trade_mae)
                status = 'none'

        if status == 'none':
            if current_candle.get('buy_signal', False) or current_candle.get('sell_signal', False):
                side = 'long' if current_candle['buy_signal'] else 'short'
                entry_price, trades_count = current_candle['open'], trades_count + 1
                current_trade_mae = 0.0
                if side == 'long': liquidation_price = entry_price * (1 - 0.99 / leverage)
                else: liquidation_price = entry_price * (1 + 0.99 / leverage)
                sl_pct = params.get('initial_sl_placement_pct', 0.1) / 100
                if side == 'long':
                    initial_sl_price = current_candle['htf_body_top'] * (1 - sl_pct)
                    tp1_price = current_candle['htf_wick_high']
                else:
                    initial_sl_price = current_candle['htf_body_bottom'] * (1 + sl_pct)
                    tp1_price = current_candle['htf_wick_low']
                status = 'part1'
                if verbose: print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | OPEN {side.upper()} | @ {entry_price:.2f} | Liq: {liquidation_price:.2f} | SL: {initial_sl_price:.2f}")

    win_rate = (wins_count / trades_count * 100) if trades_count > 0 else 0
    max_leverage, recommended_leverage = 0, 0
    if max_adverse_excursion > 0:
        max_leverage = 1 / max_adverse_excursion
        recommended_leverage = max_leverage * 0.8
        
    final_pnl_pct = ((current_capital / start_capital) - 1) * 100 if start_capital else total_pnl_pct_of_portfolio * 100
    
    if verbose:
        filter_params = params.get('sma_filter', {})
        filter_status = f"Aktiviert (Periode: {filter_params.get('period')})" if filter_params.get('enabled', False) else "Deaktiviert"
        print("\n--- Backtest-Ergebnisse (Jäger) ---")
        print(f"Zeitraum: {data.index[0].strftime('%Y-%m-%d')} -> {data.index[-1].strftime('%Y-%m-%d')}")
        print(f"Symbol: {params['symbol']} | Timeframes: {params['htf_timeframe']}/{params['ltf_timeframe']} | SMA Filter: {filter_status}")
        print(f"Parameter: Retest Tol.={params.get('retest_tolerance_pct')}% | Init SL={params.get('initial_sl_placement_pct')}% | TSL Lookback={params.get('tsl_lookback_candles')}")
        print("-" * 35)
        if start_capital:
            print(f"Startkapital: {start_capital:.2f} USDT")
            print(f"Endkapital:   {current_capital:.2f} USDT")
        print(f"Gesamt-PnL: {final_pnl_pct:.2f}% | Trades: {trades_count} | Trefferquote: {win_rate:.2f}%")
        print("-" * 35)
        print("RISIKOANALYSE:")
        print(f"Maximaler Gegenlauf (MAE): {max_adverse_excursion * 100:.2f}%")
        print(f"Maximal möglicher Hebel: {max_leverage:.2f}x")
        print(f"Empfohlener Hebel (mit 20% Puffer): {recommended_leverage:.2f}x")
        print("------------------------------------")
    
    return {
        "total_pnl_pct": final_pnl_pct, "trades_count": trades_count,
        "win_rate": win_rate, "params": params,
        "end_capital": current_capital if start_capital else None
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Strategie-Backtest für den Jäger Bot.")
    parser.add_argument('--start', required=True, help="Startdatum YYYY-MM-DD")
    parser.add_argument('--end', required=True, help="Enddatum YYYY-MM-DD")
    parser.add_argument('--symbol', help="Symbol (z.B. BTC)")
    parser.add_argument('--leverage', type=float, default=1.0, help="Hebel (Standard: 1.0)")
    parser.add_argument('--start_capital', type=float, help="Optional: Startkapital")
    parser.add_argument('--htf', help="Higher Timeframe (z.B. 1d)")
    parser.add_argument('--ltf', help="Lower Timeframe (z.B. 15m)")
    parser.add_argument('--retest_tolerance', type=float, help="Retest Toleranz in %")
    parser.add_argument('--initial_sl', type=float, help="Initial SL Platzierung in %")
    parser.add_argument('--tsl_lookback', type=int, help="TSL Lookback in Kerzen")
    parser.add_argument('--sma_filter_enabled', type=lambda x: (str(x).lower() == 'true'), help="SMA Filter an/aus (true/false)")
    parser.add_argument('--sma_period', type=int, help="SMA Filter Periode")
    args = parser.parse_args()
    
    config_path = os.path.join(os.path.dirname(__file__), '..', 'strategies', 'envelope', 'config.json')
    with open(config_path, 'r') as f: params = json.load(f)
    
    params['leverage'] = args.leverage
    if args.start_capital: params['start_capital'] = args.start_capital
    if args.symbol:
        if '/' not in args.symbol: formatted_symbol = f"{args.symbol.upper()}/USDT:USDT"
        else: formatted_symbol = args.symbol.upper()
        params['symbol'] = formatted_symbol
    if args.htf: params['htf_timeframe'] = args.htf
    if args.ltf: params['ltf_timeframe'] = args.ltf
    if args.retest_tolerance: params['retest_tolerance_pct'] = args.retest_tolerance
    if args.initial_sl: params['initial_sl_placement_pct'] = args.initial_sl
    if args.tsl_lookback: params['tsl_lookback_candles'] = args.tsl_lookback
    if args.sma_filter_enabled is not None: params['sma_filter']['enabled'] = args.sma_filter_enabled
    if args.sma_period: params['sma_filter']['period'] = args.sma_period

    print(f"\n==================== START TEST FÜR: {params['symbol']} (Hebel: {params['leverage']}x) ====================")
    htf_data = load_data_for_timeframe(params['symbol'], params['htf_timeframe'], args.start, args.end)
    ltf_data = load_data_for_timeframe(params['symbol'], params['ltf_timeframe'], args.start, args.end)
    if htf_data.empty or ltf_data.empty:
        print("Nicht genügend Daten für den Backtest vorhanden.")
    else:
        htf_data = add_sma_to_htf(htf_data, params)
        ltf_with_levels = map_levels_to_ltf(ltf_data, htf_data, params['htf_timeframe'])
        data_with_signals = calculate_jaeger_signals(ltf_with_levels, None, params)
        run_jaeger_backtest(data_with_signals, params)
    print(f"==================== ENDE TEST FÜR: {params['symbol']} =====================\n")
