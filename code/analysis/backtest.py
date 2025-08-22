import os
import sys
import json
import pandas as pd
import argparse
import warnings
from datetime import timedelta

warnings.filterwarnings("ignore", category=FutureWarning)

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from utilities.strategy_logic import calculate_momentum_signals, calculate_volatility_signals, calculate_tidal_wave_signals

def load_data(symbol, timeframe, start_date_str, end_date_str):
    cache_dir = os.path.join(os.path.dirname(__file__), 'historical_data')
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
        with open(key_path, "r") as f:
            secrets = json.load(f)
        api_setup = secrets.get('envelope', secrets.get('bitget_example'))
        bitget = BitgetFutures(api_setup)
        download_start = (pd.to_datetime(start_date_str) - timedelta(days=50)).strftime('%Y-%m-%d')
        download_end = (pd.to_datetime(end_date_str) + timedelta(days=1)).strftime('%Y-%m-%d')
        full_data = bitget.fetch_historical_ohlcv(symbol, timeframe, download_start, download_end)
        if full_data is not None and not full_data.empty:
            full_data.to_csv(cache_file)
            return full_data.loc[start_date_str:end_date_str]
        else:
            return pd.DataFrame()
    except Exception as e:
        print(f"Fehler beim Daten-Download für {timeframe}: {e}")
        return pd.DataFrame()

def run_titan_backtest(data, params, verbose=True):
    fee_pct = 0.05 / 100
    max_consecutive_losses = params.get('loss_account_max_strikes', 3)
    risk_per_trade_pct = params.get('risk_per_trade_pct', 1.0) / 100

    status, side = 'none', None
    entry_price, sl_price, tp_price = 0.0, 0.0, 0.0
    verlust_vortrag, consecutive_loss_count = 0.0, 0

    start_capital = params.get('start_capital', 1000)
    current_capital = start_capital
    trades_count, wins_count = 0, 0
    trade_log = []
    
    peak_capital = start_capital
    max_drawdown_pct = 0.0
    effective_leverages = []
    position_size_usd = 0.0
    risk_amount_usd = 0.0

    for i in range(1, len(data)):
        current_candle = data.iloc[i]
        if status == 'in_trade':
            closed_trade = False
            exit_price = 0.0
            total_pnl = 0.0

            if (side == 'long' and current_candle['low'] <= sl_price) or (side == 'short' and current_candle['high'] >= sl_price):
                exit_price = sl_price; total_pnl = -abs(risk_amount_usd); current_capital += total_pnl; consecutive_loss_count += 1; verlust_vortrag += abs(total_pnl); closed_trade = True
            elif (side == 'long' and current_candle['high'] >= tp_price) or (side == 'short' and current_candle['low'] <= tp_price):
                exit_price = tp_price; pnl_pct_of_entry = abs(exit_price / entry_price - 1); trade_pnl = position_size_usd * pnl_pct_of_entry; fee = position_size_usd * fee_pct * 2; total_pnl = trade_pnl - fee; current_capital += total_pnl; wins_count += 1; consecutive_loss_count = 0; verlust_vortrag = 0.0; closed_trade = True

            if closed_trade:
                trades_count += 1
                trade_log.append({
                    "date": str(current_candle.name.date()), "side": side, "entry": entry_price,
                    "exit": exit_price, "pnl": total_pnl, "balance": current_capital
                })
                peak_capital = max(peak_capital, current_capital)
                drawdown = (peak_capital - current_capital) / peak_capital if peak_capital > 0 else 0
                max_drawdown_pct = max(max_drawdown_pct, drawdown)
                status = 'none'
                continue
        if status == 'none':
            if consecutive_loss_count >= max_consecutive_losses:
                verlust_vortrag = 0.0; consecutive_loss_count = 0
            if pd.notna(current_candle.get('buy_signal')) and current_candle['buy_signal'] or pd.notna(current_candle.get('sell_signal')) and current_candle['sell_signal']:
                side = 'long' if current_candle['buy_signal'] else 'short'
                entry_price = data.iloc[i+1]['open'] if i+1 < len(data) else current_candle['close']
                sl_price = current_candle['sl_price']; standard_tp_price = current_candle['tp_price']
                
                risk_amount_usd = current_capital * risk_per_trade_pct
                stop_loss_distance_pct = abs(entry_price - sl_price) / entry_price if entry_price > 0 else 0
                if stop_loss_distance_pct == 0: continue
                
                position_size_usd = risk_amount_usd / stop_loss_distance_pct
                effective_leverages.append(position_size_usd / current_capital if current_capital > 0 else 0)

                standard_profit_per_unit = abs(standard_tp_price - entry_price)
                amount = position_size_usd / entry_price
                verlust_vortrag_per_unit = verlust_vortrag / amount if amount > 0 else 0
                tp_price = (entry_price + standard_profit_per_unit + verlust_vortrag_per_unit) if side == 'long' else (entry_price - standard_profit_per_unit - verlust_vortrag_per_unit)
                
                status = 'in_trade'
    
    win_rate = (wins_count / trades_count * 100) if trades_count > 0 else 0
    final_pnl_pct = ((current_capital / start_capital) - 1) * 100
    avg_effective_leverage = sum(effective_leverages) / len(effective_leverages) if effective_leverages else 0
    
    max_survivable_leverage = 1 / max_drawdown_pct if max_drawdown_pct > 0 else float('inf')
    if final_pnl_pct < 0:
        recommended_leverage = 0.0
    else:
        recommended_leverage = max_survivable_leverage * 0.8
        
    return {
        "total_pnl_pct": final_pnl_pct, "trades_count": trades_count,
        "win_rate": win_rate, "params": params, "end_capital": current_capital,
        "avg_effective_leverage": avg_effective_leverage,
        "max_drawdown_pct": max_drawdown_pct,
        "max_survivable_leverage": max_survivable_leverage,
        "trade_log": trade_log
    }
