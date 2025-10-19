# src/jaegerbot/analysis/backtester.py
import os
import pandas as pd
import numpy as np
from datetime import timedelta
import json
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from jaegerbot.utils.exchange import Exchange
from jaegerbot.utils.ann_model import prepare_data_for_ann, load_model_and_scaler, create_ann_features

def load_data(symbol, timeframe, start_date_str, end_date_str):
    cache_dir = os.path.join(PROJECT_ROOT, 'data', 'cache')
    os.makedirs(cache_dir, exist_ok=True)
    symbol_filename = symbol.replace('/', '-').replace(':', '-')
    cache_file = os.path.join(cache_dir, f"{symbol_filename}_{timeframe}.csv")
    if os.path.exists(cache_file):
        data = pd.read_csv(cache_file, index_col='timestamp', parse_dates=True)
        try:
            if data.index.min() <= pd.to_datetime(start_date_str, utc=True) and data.index.max() >= pd.to_datetime(end_date_str, utc=True):
                return data.loc[start_date_str:end_date_str]
        except Exception:
            pass
    print(f"Starte Download für {symbol} ({timeframe}) von der Börse...")
    try:
        with open(os.path.join(PROJECT_ROOT, 'secret.json'), "r") as f: secrets = json.load(f)
        api_setup = secrets.get('jaegerbot')[0] 
        exchange = Exchange(api_setup)
        full_data = exchange.fetch_historical_ohlcv(symbol, timeframe, start_date_str, end_date_str)
        if not full_data.empty:
            full_data.to_csv(cache_file)
            return full_data
    except Exception as e:
        print(f"Fehler beim Daten-Download: {e}")
    return pd.DataFrame()

def run_ann_backtest(data, params, model_paths, start_capital=1000, use_macd_filter=False, htf_data=None, timeframe=None, verbose=False):
    model, scaler = load_model_and_scaler(model_paths['model'], model_paths['scaler'])
    if not model or not scaler: raise Exception("Modell/Scaler nicht gefunden!")
    
    if not timeframe:
        raise ValueError("Backtester benötigt ein 'timeframe' Argument für die Daten-Vorbereitung!")
    
    # Hier wird die Statusmeldung unterdrückt (verbose=False)
    X, _ = prepare_data_for_ann(data.copy(), timeframe, verbose=verbose)

    if X.empty: return {"total_pnl_pct": 0, "trades_count": 0, "win_rate": 0, "max_drawdown_pct": 1, "end_capital": start_capital}
    
    data_with_features = data.loc[X.index].copy()
    features_scaled = scaler.transform(X)
    predictions = model.predict(features_scaled, verbose=0).flatten()
    data_with_features['prediction'] = predictions

    pred_threshold = params.get('prediction_threshold', 0.6)
    risk_reward_ratio = params.get('risk_reward_ratio', 1.5)
    risk_per_trade_pct = params.get('risk_per_trade_pct', 1.0) / 100
    activation_rr = params.get('trailing_stop_activation_rr', 2.0)
    callback_rate = params.get('trailing_stop_callback_rate_pct', 1.0) / 100
    leverage = params.get('leverage', 10)
    fee_pct = 0.05 / 100
    
    current_capital, trades_count, wins_count = start_capital, 0, 0
    peak_capital, max_drawdown_pct = start_capital, 0.0
    position = None

    for i in range(len(data_with_features)):
        current = data_with_features.iloc[i]
        if position:
            exit_price, reason = None, None
            if position['side'] == 'long':
                if not position['trailing_active'] and current['high'] >= position['take_profit']: exit_price = position['take_profit']
                elif current['low'] <= position['stop_loss']: exit_price = position['stop_loss']
                elif not position['trailing_active'] and current['high'] >= position['activation_price']: position['trailing_active'] = True
                if position['trailing_active']:
                    position['peak_price'] = max(position['peak_price'], current['high'])
                    trailing_sl = position['peak_price'] * (1 - callback_rate)
                    if current['low'] <= trailing_sl: exit_price = trailing_sl
            elif position['side'] == 'short':
                if not position['trailing_active'] and current['low'] <= position['take_profit']: exit_price = position['take_profit']
                elif current['high'] >= position['stop_loss']: exit_price = position['stop_loss']
                elif not position['trailing_active'] and current['low'] <= position['activation_price']: position['trailing_active'] = True
                if position['trailing_active']:
                    position['peak_price'] = min(position['peak_price'], current['low'])
                    trailing_sl = position['peak_price'] * (1 + callback_rate)
                    if current['high'] >= trailing_sl: exit_price = trailing_sl
            if exit_price:
                pnl_pct = (exit_price / position['entry_price'] - 1) if position['side'] == 'long' else (1 - exit_price / position['entry_price'])
                notional_value = position['margin_used'] * leverage
                pnl_usd = notional_value * pnl_pct
                total_fees = notional_value * fee_pct * 2
                current_capital += pnl_usd - total_fees
                if pnl_usd - total_fees > 0: wins_count += 1
                trades_count += 1
                position = None
                peak_capital = max(peak_capital, current_capital)
                drawdown = (peak_capital - current_capital) / peak_capital if peak_capital > 0 else 0
                max_drawdown_pct = max(max_drawdown_pct, drawdown)
                if current_capital <= 0: break
        
        if not position:
            side = 'long' if current['prediction'] >= pred_threshold else 'short' if current['prediction'] <= (1 - pred_threshold) else None
            trade_allowed = True
            if side and use_macd_filter and htf_data is not None:
                htf_candle = htf_data[htf_data.index <= current.name].iloc[-1]
                htf_macd_diff = htf_candle['macd_diff']
                is_bullish_trend = htf_macd_diff > 0
                is_bearish_trend = htf_macd_diff < 0
                if side == 'long' and not is_bullish_trend: trade_allowed = False
                elif side == 'short' and not is_bearish_trend: trade_allowed = False

            if side and trade_allowed:
                entry_price = current['close']
                risk_amount_usd = current_capital * risk_per_trade_pct
                sl_distance_pct = 0.01 
                notional_value = risk_amount_usd / sl_distance_pct
                margin_used = notional_value / leverage
                if margin_used > current_capital: continue
                stop_loss_distance = entry_price * sl_distance_pct
                stop_loss = entry_price - stop_loss_distance if side == 'long' else entry_price + stop_loss_distance
                position = {'side': side, 'entry_price': entry_price, 'stop_loss': stop_loss,
                            'take_profit': entry_price + (entry_price - stop_loss) * risk_reward_ratio if side == 'long' else entry_price - (stop_loss - entry_price) * risk_reward_ratio,
                            'margin_used': margin_used, 'trailing_active': False, 
                            'activation_price': entry_price + (entry_price - stop_loss) * activation_rr if side == 'long' else entry_price - (stop_loss - entry_price) * activation_rr,
                            'peak_price': entry_price}

    win_rate = (wins_count / trades_count * 100) if trades_count > 0 else 0
    final_pnl_pct = ((current_capital - start_capital) / start_capital) * 100 if start_capital > 0 else 0
    return {"total_pnl_pct": final_pnl_pct, "trades_count": trades_count, "win_rate": win_rate, "max_drawdown_pct": max_drawdown_pct, "end_capital": current_capital}
