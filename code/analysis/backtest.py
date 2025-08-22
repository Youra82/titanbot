import os
import sys
import json
import pandas as pd
import argparse
import warnings
from datetime import timedelta

warnings.filterwarnings("ignore", category=FutureWarning)

# Systempfad hinzufügen, um Utilities zu finden
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from utilities.strategy_logic import calculate_momentum_signals, calculate_volatility_signals, calculate_tidal_wave_signals

# --- Datenladefunktion ---
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
            print(f"Lade {timeframe}-Daten für {symbol} aus dem Cache.")
            return data.loc[start_date_str:end_date_str]
            
    print(f"Cache für {timeframe} nicht ausreichend. Lade neue Daten...")
    try:
        from utilities.bitget_futures import BitgetFutures
        project_root = os.path.join(os.path.dirname(__file__), '..', '..')
        key_path = os.path.abspath(os.path.join(project_root, 'secret.json'))
        
        # +++ HIER IST DIE KORREKTUR +++
        # Die Datei wird jetzt nur noch einmal korrekt geladen.
        with open(key_path, "r") as f:
            secrets = json.load(f)
        api_setup = secrets.get('envelope', secrets.get('bitget_example'))
        # +++ ENDE DER KORREKTUR +++

        bitget = BitgetFutures(api_setup)
        
        download_start = (pd.to_datetime(start_date_str) - timedelta(days=50)).strftime('%Y-%m-%d')
        download_end = (pd.to_datetime(end_date_str) + timedelta(days=1)).strftime('%Y-%m-%d')
        
        full_data = bitget.fetch_historical_ohlcv(symbol, timeframe, download_start, download_end)
        if full_data is not None and not full_data.empty:
            full_data.to_csv(cache_file)
            return full_data.loc[start_date_str:end_date_str]
        else:
            print(f"--> FEHLER: Keine historischen Daten für {timeframe} erhalten.")
            return pd.DataFrame()
    except Exception as e:
        print(f"Fehler beim Daten-Download für {timeframe}: {e}")
        return pd.DataFrame()

# --- Die Titanbot Backtest-Engine (unverändert) ---
def run_titan_backtest(data, params, verbose=True):
    leverage = params.get('leverage', 1.0)
    fee_pct = 0.05 / 100
    max_consecutive_losses = params.get('loss_account_max_strikes', 3)

    status, side = 'none', None
    entry_price, sl_price, tp_price, liquidation_price = 0.0, 0.0, 0.0, 0.0
    verlust_vortrag, consecutive_loss_count = 0.0, 0

    start_capital = params.get('start_capital', 1000)
    current_capital = start_capital
    trade_size_pct = params.get('trade_size_pct', 10) / 100
    trades_count, wins_count = 0, 0
    max_adverse_excursion, current_trade_mae = 0.0, 0.0

    for i in range(1, len(data)):
        current_candle = data.iloc[i]

        if status == 'in_trade':
            adverse_move = (entry_price - current_candle['low']) / entry_price if side == 'long' else (current_candle['high'] - entry_price) / entry_price
            current_trade_mae = max(current_trade_mae, adverse_move)

            liquidated = (side == 'long' and current_candle['low'] <= liquidation_price) or \
                         (side == 'short' and current_candle['high'] >= liquidation_price)
            if liquidated:
                pnl = - (current_capital * trade_size_pct)
                current_capital += pnl
                trades_count += 1
                consecutive_loss_count += 1
                verlust_vortrag += abs(pnl)
                if verbose: print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | LIQUIDATION | PnL: {pnl:.2f} USDT")
                status = 'none'
                continue

            stop_hit = (side == 'long' and current_candle['low'] <= sl_price) or \
                       (side == 'short' and current_candle['high'] >= sl_price)
            if stop_hit:
                exit_price = sl_price
                pnl_pct = (exit_price / entry_price - 1) if side == 'long' else (1 - exit_price / entry_price)
                trade_pnl = current_capital * trade_size_pct * pnl_pct * leverage
                fee = current_capital * trade_size_pct * fee_pct * 2 * leverage
                total_pnl = trade_pnl - fee
                current_capital += total_pnl
                trades_count += 1
                consecutive_loss_count += 1
                verlust_vortrag += abs(total_pnl)
                max_adverse_excursion = max(max_adverse_excursion, current_trade_mae)
                if verbose: print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | STOP-LOSS   | PnL: {total_pnl:.2f} USDT")
                status = 'none'
                continue

            tp_hit = (side == 'long' and current_candle['high'] >= tp_price) or \
                     (side == 'short' and current_candle['low'] <= tp_price)
            if tp_hit:
                exit_price = tp_price
                pnl_pct = (exit_price / entry_price - 1) if side == 'long' else (1 - exit_price / entry_price)
                trade_pnl = current_capital * trade_size_pct * pnl_pct * leverage
                fee = current_capital * trade_size_pct * fee_pct * 2 * leverage
                total_pnl = trade_pnl - fee
                current_capital += total_pnl
                trades_count += 1
                wins_count += 1
                consecutive_loss_count = 0
                verlust_vortrag = 0.0
                max_adverse_excursion = max(max_adverse_excursion, current_trade_mae)
                if verbose: print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | TAKE-PROFIT | PnL: {total_pnl:.2f} USDT")
                status = 'none'
                continue

        if status == 'none':
            if consecutive_loss_count >= max_consecutive_losses:
                if verbose: print(f"INFO: Verlust-Konto nach {consecutive_loss_count} Verlusten zurückgesetzt.")
                verlust_vortrag = 0.0
                consecutive_loss_count = 0

            if pd.notna(current_candle.get('buy_signal')) and current_candle['buy_signal'] or \
               pd.notna(current_candle.get('sell_signal')) and current_candle['sell_signal']:
                side = 'long' if current_candle['buy_signal'] else 'short'
                entry_price = data.iloc[i+1]['open'] if i+1 < len(data) else current_candle['close']
                current_trade_mae = 0.0

                standard_sl_price = current_candle['sl_price']
                standard_tp_price = current_candle['tp_price']

                standard_profit_per_unit = abs(standard_tp_price - entry_price)
                margin = current_capital * trade_size_pct
                amount = (margin * leverage) / entry_price
                verlust_vortrag_per_unit = verlust_vortrag / amount if amount > 0 else 0

                final_tp_price = (entry_price + standard_profit_per_unit + verlust_vortrag_per_unit) if side == 'long' \
                    else (entry_price - standard_profit_per_unit - verlust_vortrag_per_unit)

                sl_price, tp_price = standard_sl_price, final_tp_price
                liquidation_price = entry_price * (1 - 0.99 / leverage) if side == 'long' else entry_price * (1 + 0.99 / leverage)
                status = 'in_trade'
                if verbose:
                    print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | OPEN {side.upper()} | @ {entry_price:.4f} | TP: {tp_price:.4f} (Vortrag: {verlust_vortrag:.2f}) | SL: {sl_price:.4f}")

    win_rate = (wins_count / trades_count * 100) if trades_count > 0 else 0
    max_leverage = 1 / max_adverse_excursion if max_adverse_excursion > 0 else float('inf')
    recommended_leverage = max_leverage * 0.8
    final_pnl_pct = ((current_capital / start_capital) - 1) * 100

    if verbose:
        print("\n--- Backtest-Ergebnisse (Titanbot) ---")
        print(f"Strategie: {params.get('strategy_name', 'Unbekannt')}")
        print("-" * 35)
        print(f"Startkapital: {start_capital:.2f} USDT")
        print(f"Endkapital:   {current_capital:.2f} USDT")
        print(f"Gesamt-PnL: {final_pnl_pct:.2f}% | Trades: {trades_count} | Trefferquote: {win_rate:.2f}%")
        print("-" * 35)
        print("RISIKOANALYSE:")
        print(f"Maximaler Gegenlauf (MAE): {max_adverse_excursion * 100:.2f}%")
        print(f"Maximal möglicher Hebel: {max_leverage:.2f}x")
        print(f"Empfohlener Hebel (20% Puffer): {recommended_leverage:.2f}x")
        print("------------------------------------")

    return {
        "total_pnl_pct": final_pnl_pct, "trades_count": trades_count,
        "win_rate": win_rate, "params": params, "end_capital": current_capital,
        "recommended_leverage": recommended_leverage, "max_leverage": max_leverage
    }
