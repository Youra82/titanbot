# src/titanbot/analysis/backtester.py
import os
import pandas as pd
import numpy as np
import json
import sys
from tqdm import tqdm
import ta # Import für ATR hinzugefügt

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from titanbot.utils.exchange import Exchange
from titanbot.strategy.smc_engine import SMCEngine, Bias
from titanbot.strategy.trade_logic import get_titan_signal # Wir nutzen die Live-Logik!

# Die `load_data` Funktion bleibt identisch
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
            return full_data.loc[start_date_str:end_date_str]
    except Exception as e:
        print(f"Fehler beim Daten-Download: {e}")
    return pd.DataFrame()

def run_smc_backtest(data, smc_params, risk_params, start_capital=1000, verbose=False):
    """
    Führt einen chronologischen Backtest für die SMC-Strategie durch,
    jetzt mit ATR-basiertem Stop-Loss.
    """
    if data.empty or len(data) < 15: # Brauchen genug Daten für ATR(14)
        print("WARNUNG: Nicht genügend Daten für Backtest mit ATR.")
        return {"total_pnl_pct": 0, "trades_count": 0, "win_rate": 0, "max_drawdown_pct": 1.0, "end_capital": start_capital}

    # --- 0. Berechne ATR (einmalig) ---
    try:
        atr_indicator = ta.volatility.AverageTrueRange(high=data['high'], low=data['low'], close=data['close'], window=14)
        data['atr'] = atr_indicator.average_true_range()
        data.dropna(inplace=True) # Entferne Zeilen, wo ATR NaN ist
        if data.empty:
             print("WARNUNG: Nach ATR-Berechnung keine Daten mehr übrig.")
             return {"total_pnl_pct": 0, "trades_count": 0, "win_rate": 0, "max_drawdown_pct": 1.0, "end_capital": start_capital}
    except Exception as e:
        print(f"FEHLER bei ATR-Berechnung: {e}")
        return {"total_pnl_pct": -999, "trades_count": 0, "win_rate": 0, "max_drawdown_pct": 1.0, "end_capital": start_capital}


    # --- 1. Parameter extrahieren ---
    risk_reward_ratio = risk_params.get('risk_reward_ratio', 1.5)
    risk_per_trade_pct = risk_params.get('risk_per_trade_pct', 1.0) / 100
    activation_rr = risk_params.get('trailing_stop_activation_rr', 2.0)
    callback_rate = risk_params.get('trailing_stop_callback_rate_pct', 1.0) / 100
    leverage = risk_params.get('leverage', 10)
    fee_pct = 0.05 / 100
    atr_multiplier_sl = 2.0 # Standard ATR Multiplikator für SL (kann optimiert werden!)

    # --- 2. SMC-Analyse durchführen (einmalig auf Daten mit ATR) ---
    if verbose: print("Starte SMC-Engine-Analyse...")
    engine = SMCEngine(settings=smc_params)
    # Wichtig: process_dataframe braucht nur OHLC, nicht ATR
    smc_results = engine.process_dataframe(data[['open', 'high', 'low', 'close']].copy())
    if verbose: print("SMC-Analyse abgeschlossen.")

    # --- 3. Backtest-Variablen initialisieren ---
    current_capital = start_capital
    peak_capital = start_capital
    max_drawdown_pct = 0.0
    trades_count = 0
    wins_count = 0
    position = None

    iterator = tqdm(data.iterrows(), total=len(data), desc="Backtesting") if verbose else data.iterrows()

    for timestamp, current_candle in iterator:

        # --- 4. Positions-Management (Stop-Loss & Take-Profit) ---
        if position:
            exit_price = None
            if position['side'] == 'long':
                if not position['trailing_active'] and current_candle['high'] >= position['activation_price']:
                    position['trailing_active'] = True
                if position['trailing_active']:
                    position['peak_price'] = max(position['peak_price'], current_candle['high'])
                    trailing_sl = position['peak_price'] * (1 - callback_rate)
                    position['stop_loss'] = max(position['stop_loss'], trailing_sl)
                if current_candle['low'] <= position['stop_loss']: exit_price = position['stop_loss']
                elif not position['trailing_active'] and current_candle['high'] >= position['take_profit']: exit_price = position['take_profit']
            elif position['side'] == 'short':
                if not position['trailing_active'] and current_candle['low'] <= position['activation_price']:
                    position['trailing_active'] = True
                if position['trailing_active']:
                    position['peak_price'] = min(position['peak_price'], current_candle['low'])
                    trailing_sl = position['peak_price'] * (1 + callback_rate)
                    position['stop_loss'] = min(position['stop_loss'], trailing_sl)
                if current_candle['high'] >= position['stop_loss']: exit_price = position['stop_loss']
                elif not position['trailing_active'] and current_candle['low'] <= position['take_profit']: exit_price = position['take_profit']

            if exit_price:
                pnl_pct = (exit_price / position['entry_price'] - 1) if position['side'] == 'long' else (1 - exit_price / position['entry_price'])
                notional_value = position['margin_used'] * leverage # Korrekt: Notional = Margin * Hebel
                pnl_usd = notional_value * pnl_pct
                total_fees = notional_value * fee_pct * 2
                current_capital += (pnl_usd - total_fees)
                if (pnl_usd - total_fees) > 0: wins_count += 1
                trades_count += 1
                position = None
                peak_capital = max(peak_capital, current_capital)
                drawdown = (peak_capital - current_capital) / peak_capital if peak_capital > 0 else 0
                max_drawdown_pct = max(max_drawdown_pct, drawdown)
                if current_capital <= 0: break # Liquidation

        # --- 5. Einstiegs-Logik (nur wenn keine Position offen ist) ---
        if not position and current_capital > 0:
            side, _ = get_titan_signal(smc_results, current_candle, params={})

            if side:
                entry_price = current_candle['close']
                
                # *** NEU: ATR-basierter Stop-Loss ***
                current_atr = current_candle.get('atr', 0) # Hole ATR Wert
                if pd.isna(current_atr) or current_atr <= 0:
                    # print(f"WARNUNG: Ungültiger ATR ({current_atr}) bei {timestamp}. Überspringe Signal.") # Debug
                    continue # Überspringe Trade, wenn ATR ungültig
                
                sl_distance = current_atr * atr_multiplier_sl # z.B. 2 * ATR
                # *** ENDE NEU ***
                
                if sl_distance == 0: continue # Sollte nicht passieren, aber sicher ist sicher

                risk_amount_usd = current_capital * risk_per_trade_pct
                
                # *** NEU: Notional Value basierend auf $ SL-Abstand ***
                # Risiko in $ / (SL-Abstand in $ / Entry Preis) = Notional Value
                # Vereinfacht: Risiko in $ / (SL-Abstand / Entry Preis)
                sl_distance_pct_equivalent = sl_distance / entry_price # SL-Abstand als % vom Entry
                if sl_distance_pct_equivalent == 0 : continue # Vermeide Division durch 0
                
                notional_value = risk_amount_usd / sl_distance_pct_equivalent
                margin_used = notional_value / leverage
                # *** ENDE NEU ***

                if margin_used > current_capital: continue

                stop_loss = entry_price - sl_distance if side == 'buy' else entry_price + sl_distance
                take_profit = entry_price + sl_distance * risk_reward_ratio if side == 'buy' else entry_price - sl_distance * risk_reward_ratio
                activation_price = entry_price + sl_distance * activation_rr if side == 'buy' else entry_price - sl_distance * activation_rr

                position = {
                    'side': 'long' if side == 'buy' else 'short',
                    'entry_price': entry_price,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'margin_used': margin_used, # Korrigiert: Margin statt Notional hier speichern
                    'trailing_active': False,
                    'activation_price': activation_price,
                    'peak_price': entry_price
                }

    # --- 6. Ergebnisse zurückgeben ---
    win_rate = (wins_count / trades_count * 100) if trades_count > 0 else 0
    final_pnl_pct = ((current_capital - start_capital) / start_capital) * 100 if start_capital > 0 else 0
    
    # Stelle sicher, dass Endkapital nicht negativ ist (z.B. durch Rundungsfehler bei Liquidation)
    final_capital = max(0, current_capital)

    return {
        "total_pnl_pct": final_pnl_pct,
        "trades_count": trades_count,
        "win_rate": win_rate,
        "max_drawdown_pct": max_drawdown_pct, # Wird jetzt als Dezimalzahl zurückgegeben
        "end_capital": final_capital
    }
