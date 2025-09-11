# code/analysis/backtest.py

import os
import sys
import pandas as pd
import numpy as np
import warnings
from datetime import timedelta
import json

warnings.filterwarnings("ignore", category=FutureWarning)

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from utilities.strategy_logic import calculate_smc_indicators

def load_data(symbol, timeframe, start_date_str, end_date_str):
    # Diese Funktion bleibt unverändert
    cache_dir = os.path.join(os.path.dirname(__file__), '..', 'analysis', 'historical_data')
    os.makedirs(cache_dir, exist_ok=True)
    symbol_filename = symbol.replace('/', '-').replace(':', '-')
    cache_file = os.path.join(cache_dir, f"{symbol_filename}_{timeframe}.csv")
    if os.path.exists(cache_file):
        data = pd.read_csv(cache_file, index_col='timestamp', parse_dates=True)
        data.index = pd.to_datetime(data.index, utc=True)
        required_start = pd.to_datetime(start_date_str, utc=True)
        if data.index.min() <= required_start and data.index.max() >= pd.to_datetime(end_date_str, utc=True):
            return data.loc[start_date_str:end_date_str]
    try:
        from utilities.bitget_futures import BitgetFutures
        project_root = os.path.join(os.path.dirname(__file__), '..', '..')
        key_path = os.path.abspath(os.path.join(project_root, 'secret.json'))
        with open(key_path, "r") as f: secrets = json.load(f)
        api_setup = secrets.get('envelope', secrets.get('bitget_example'))
        bitget = BitgetFutures(api_setup)
        download_start = (pd.to_datetime(start_date_str) - timedelta(days=50)).strftime('%Y-%m-%d')
        download_end = (pd.to_datetime(end_date_str) + timedelta(days=1)).strftime('%Y-%m-%d')
        full_data = bitget.fetch_historical_ohlcv(symbol, timeframe, download_start, download_end)
        if full_data is not None and not full_data.empty:
            full_data.to_csv(cache_file)
            return full_data.loc[start_date_str:end_date_str]
        else: return pd.DataFrame()
    except Exception as e:
        print(f"Fehler beim Daten-Download für {timeframe}: {e}"); return pd.DataFrame()

# --- START: Überarbeitete Backtest-Funktion mit Trailing Stop ---
def run_smc_backtest(data, params):
    # Lade alle Parameter
    risk_reward_ratio = params.get('risk_reward_ratio', 3.0)
    risk_per_trade_pct = params.get('risk_per_trade_pct', 1.0) / 100
    fee_pct = 0.05 / 100
    start_capital = params.get('start_capital', 1000)
    
    # Neue Parameter für den Trailing Stop
    activation_rr = params.get('trailing_stop_activation_rr', 2.0)
    callback_rate = params.get('trailing_stop_callback_rate_pct', 1.0) / 100

    data = calculate_smc_indicators(data.copy(), params)
    
    current_capital = start_capital
    trades_count, wins_count = 0, 0
    trade_log = []
    peak_capital = start_capital
    max_drawdown_pct = 0.0
    position = None

    for i in range(1, len(data)):
        current = data.iloc[i]
        prev = data.iloc[i-1]

        if position:
            exit_price, reason = None, None
            
            # Management für eine Long-Position
            if position['side'] == 'long':
                # Stop-Loss-Prüfung (immer aktiv)
                if current['low'] <= position['stop_loss']:
                    exit_price, reason = position['stop_loss'], "Stop-Loss"
                
                # Wenn Trailing Stop noch NICHT aktiv ist
                elif not position['trailing_active']:
                    if current['high'] >= position['take_profit']:
                        exit_price, reason = position['take_profit'], "Take-Profit"
                    # Prüfung, ob Trailing Stop aktiviert werden soll
                    elif current['high'] >= position['activation_price']:
                        position['trailing_active'] = True
                        position['peak_price'] = current['high']
                        logger.debug(f"Trailing Stop für Long aktiviert bei {current.name}")
                
                # Wenn Trailing Stop AKTIV ist
                if position['trailing_active']:
                    position['peak_price'] = max(position['peak_price'], current['high'])
                    trailing_sl_price = position['peak_price'] * (1 - callback_rate)
                    if current['low'] <= trailing_sl_price:
                        exit_price, reason = trailing_sl_price, "Trailing Stop"

            # Management für eine Short-Position
            elif position['side'] == 'short':
                if current['high'] >= position['stop_loss']:
                    exit_price, reason = position['stop_loss'], "Stop-Loss"
                
                elif not position['trailing_active']:
                    if current['low'] <= position['take_profit']:
                        exit_price, reason = position['take_profit'], "Take-Profit"
                    elif current['low'] <= position['activation_price']:
                        position['trailing_active'] = True
                        position['peak_price'] = current['low']
                        logger.debug(f"Trailing Stop für Short aktiviert bei {current.name}")

                if position['trailing_active']:
                    position['peak_price'] = min(position['peak_price'], current['low'])
                    trailing_sl_price = position['peak_price'] * (1 + callback_rate)
                    if current['high'] >= trailing_sl_price:
                        exit_price, reason = trailing_sl_price, "Trailing Stop"
            
            # Trade schließen, falls ein Exit-Grund gefunden wurde
            if exit_price:
                pnl_pct = (exit_price / position['entry_price'] - 1) if position['side'] == 'long' else (1 - exit_price / position['entry_price'])
                pnl_usd = position['size_usd'] * pnl_pct
                total_fees = (position['size_usd'] * fee_pct) * 2
                
                current_capital += pnl_usd - total_fees
                trades_count += 1
                if pnl_usd > 0: wins_count += 1
                
                trade_log.append({
                    "timestamp": str(current.name), "side": position['side'], "entry": position['entry_price'],
                    "exit": exit_price, "pnl": pnl_usd - total_fees, "balance": current_capital, "reason": reason
                })
                position = None
                peak_capital = max(peak_capital, current_capital)
                drawdown = (peak_capital - current_capital) / peak_capital if peak_capital > 0 else 0
                max_drawdown_pct = max(max_drawdown_pct, drawdown)
                if current_capital <= 0: break

        # Logik zur Eröffnung neuer Positionen (bleibt unverändert)
        if not position and not np.isnan(prev['bos_level']) and not np.isnan(prev['ob_high']):
            entry_price, stop_loss, side = None, None, None
            if prev['trend'] == 1 and current['low'] <= prev['ob_high'] and current['high'] > prev['ob_high']:
                entry_price, stop_loss, side = prev['ob_high'], prev['ob_low'], 'long'
            elif prev['trend'] == -1 and current['high'] >= prev['ob_low'] and current['low'] < prev['ob_low']:
                entry_price, stop_loss, side = prev['ob_low'], prev['ob_high'], 'short'

            if entry_price and stop_loss and abs(entry_price - stop_loss) > 0:
                risk_distance = abs(entry_price - stop_loss)
                sl_distance_pct = risk_distance / entry_price
                if sl_distance_pct == 0: continue
                
                size_usd = (current_capital * risk_per_trade_pct) / sl_distance_pct
                take_profit = entry_price + risk_distance * risk_reward_ratio if side == 'long' else entry_price - risk_distance * risk_reward_ratio
                activation_price = entry_price + risk_distance * activation_rr if side == 'long' else entry_price - risk_distance * activation_rr
                
                position = {
                    'side': side, 'entry_price': entry_price, 'stop_loss': stop_loss,
                    'take_profit': take_profit, 'size_usd': size_usd,
                    'trailing_active': False, 'activation_price': activation_price, 'peak_price': entry_price
                }
    
    win_rate = (wins_count / trades_count * 100) if trades_count > 0 else 0
    final_pnl_pct = ((current_capital - start_capital) / start_capital) * 100
    
    return {
        "total_pnl_pct": final_pnl_pct, "trades_count": trades_count,
        "win_rate": win_rate, "params": params, "end_capital": current_capital,
        "max_drawdown_pct": max_drawdown_pct, "trade_log": trade_log
    }
# --- ENDE: Überarbeitete Backtest-Funktion ---
