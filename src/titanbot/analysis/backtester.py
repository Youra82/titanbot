# src/titanbot/analysis/backtester.py (MIT ROBUSTER VERZEICHNISERSTELLUNG)
import os
import pandas as pd
import numpy as np
import json
import sys
from tqdm import tqdm
import ta

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from titanbot.utils.exchange import Exchange
from titanbot.strategy.smc_engine import SMCEngine, Bias
from titanbot.strategy.trade_logic import get_titan_signal

# Globale Variable für Secrets, um wiederholtes Lesen zu vermeiden
secrets_cache = None

def load_data(symbol, timeframe, start_date_str, end_date_str):
    global secrets_cache # Zugriff auf globale Variable

    data_dir = os.path.join(PROJECT_ROOT, 'data')
    cache_dir = os.path.join(data_dir, 'cache')
    symbol_filename = symbol.replace('/', '-').replace(':', '-')
    cache_file = os.path.join(cache_dir, f"{symbol_filename}_{timeframe}.csv")

    # --- ROBUSTERE VERZEICHNISERSTELLUNG ---
    try:
        # Erstelle zuerst das übergeordnete Verzeichnis, falls es fehlt
        if not os.path.exists(data_dir):
             os.makedirs(data_dir)
             print(f"Info: Verzeichnis '{data_dir}' erstellt.")
        # Erstelle dann das Cache-Verzeichnis
        # exist_ok=True verhindert Fehler, wenn es bereits existiert
        os.makedirs(cache_dir, exist_ok=True)
    except OSError as e:
        print(f"FATAL: Konnte Cache-Verzeichnis '{cache_dir}' nicht erstellen: {e}")
        return pd.DataFrame() # Abbruch, wenn Verzeichnis nicht erstellt werden kann
    # --- ENDE VERZEICHNISERSTELLUNG ---


    # --- Cache-Prüfung ---
    if os.path.exists(cache_file):
        try:
            data = pd.read_csv(cache_file, index_col='timestamp', parse_dates=True)
            # Prüfen ob Daten aktuell genug UND vollständig sind
            data_start = data.index.min()
            data_end = data.index.max()
            req_start = pd.to_datetime(start_date_str, utc=True)
            req_end = pd.to_datetime(end_date_str, utc=True)

            # Ist der angefragte Zeitraum *innerhalb* der Cache-Daten?
            if data_start <= req_start and data_end >= req_end:
                 print(f"Info: Lade Daten für {symbol} {timeframe} aus Cache.")
                 return data.loc[start_date_str:end_date_str]
            else:
                 print(f"Info: Cache für {symbol} {timeframe} nicht aktuell genug oder unvollständig. Lade neu.")
        except Exception as e:
            print(f"WARNUNG: Fehler beim Lesen der Cache-Datei '{cache_file}': {e}. Lade neu.")
            try: os.remove(cache_file) # Korrupte Cache-Datei löschen
            except OSError: pass

    # --- Download ---
    print(f"Starte Download für {symbol} ({timeframe}) von der Börse...")
    try:
        # Lade API Keys nur einmal pro Skriptlauf
        if secrets_cache is None:
             with open(os.path.join(PROJECT_ROOT, 'secret.json'), "r") as f: secrets_cache = json.load(f)
        
        # Prüfe, ob 'jaegerbot' Schlüssel existiert und eine Liste ist
        if 'jaegerbot' not in secrets_cache or not isinstance(secrets_cache['jaegerbot'], list) or not secrets_cache['jaegerbot']:
             print("FEHLER: 'jaegerbot' Schlüssel in secret.json fehlt, ist keine Liste oder ist leer.")
             return pd.DataFrame()
             
        api_setup = secrets_cache['jaegerbot'][0]
        exchange = Exchange(api_setup) # Exchange Instanz wird nur hier erstellt
        
        # Prüfe ob Exchange-Initialisierung erfolgreich war
        if not exchange.markets:
             print("FEHLER: Exchange konnte nicht initialisiert werden (Märkte nicht geladen). Download abgebrochen.")
             return pd.DataFrame()
             
        full_data = exchange.fetch_historical_ohlcv(symbol, timeframe, start_date_str, end_date_str)
        
        if not full_data.empty:
            try:
                # Speichere die *vollen* heruntergeladenen Daten
                full_data.to_csv(cache_file)
                print(f"Info: Daten für {symbol} {timeframe} in Cache gespeichert.")
                # Filtere *nach* dem Speichern auf den angefragten Zeitraum
                req_start_dt = pd.to_datetime(start_date_str, utc=True)
                req_end_dt = pd.to_datetime(end_date_str, utc=True)
                return full_data.loc[req_start_dt:req_end_dt]
            except Exception as e_save:
                 print(f"FEHLER beim Speichern der Cache-Datei '{cache_file}': {e_save}")
                 # Gebe trotzdem die Daten zurück, wenn der Download erfolgreich war
                 return full_data.loc[start_date_str:end_date_str]
        else:
            # fetch_historical_ohlcv gibt bereits Meldungen aus, wenn keine Daten gefunden wurden
            return pd.DataFrame()
            
    except FileNotFoundError:
         print(f"FEHLER: secret.json nicht gefunden unter {PROJECT_ROOT}. Download nicht möglich.")
         return pd.DataFrame()
    except KeyError:
         print("FEHLER: 'jaegerbot' Schlüssel oder notwendige API-Keys in secret.json nicht gefunden.")
         return pd.DataFrame()
    except Exception as e:
        print(f"FEHLER beim Daten-Download-Prozess: {e}")
        import traceback
        traceback.print_exc() # Gib mehr Details aus bei unerwarteten Fehlern
    return pd.DataFrame()


# --- run_smc_backtest Funktion bleibt wie in der vorherigen Antwort ---
# (mit ATR-SL und Debug-Ausgaben)
def run_smc_backtest(data, smc_params, risk_params, start_capital=1000, verbose=False):
    if data.empty or len(data) < 15: # Brauchen genug Daten für ATR(14)
        print("WARNUNG: Nicht genügend Daten für Backtest mit ATR.")
        return {"total_pnl_pct": 0, "trades_count": 0, "win_rate": 0, "max_drawdown_pct": 1.0, "end_capital": start_capital}
    try:
        atr_indicator = ta.volatility.AverageTrueRange(high=data['high'], low=data['low'], close=data['close'], window=14)
        data['atr'] = atr_indicator.average_true_range()
        data.dropna(subset=['atr'], inplace=True) # Nur Zeilen entfernen, wo ATR NaN ist
        if data.empty:
             print("WARNUNG: Nach ATR-Berechnung keine Daten mehr übrig.")
             return {"total_pnl_pct": 0, "trades_count": 0, "win_rate": 0, "max_drawdown_pct": 1.0, "end_capital": start_capital}
    except Exception as e:
        print(f"FEHLER bei ATR-Berechnung: {e}")
        return {"total_pnl_pct": -999, "trades_count": 0, "win_rate": 0, "max_drawdown_pct": 1.0, "end_capital": start_capital}

    risk_reward_ratio = risk_params.get('risk_reward_ratio', 1.5)
    risk_per_trade_pct = risk_params.get('risk_per_trade_pct', 1.0) / 100
    activation_rr = risk_params.get('trailing_stop_activation_rr', 2.0)
    callback_rate = risk_params.get('trailing_stop_callback_rate_pct', 1.0) / 100
    leverage = risk_params.get('leverage', 10)
    fee_pct = 0.05 / 100
    atr_multiplier_sl = 2.0 # Standard ATR Multiplikator für SL (kann optimiert werden!)

    if verbose: print("Starte SMC-Engine-Analyse...")
    engine = SMCEngine(settings=smc_params)
    smc_results = engine.process_dataframe(data[['open', 'high', 'low', 'close']].copy())
    if verbose: print("SMC-Analyse abgeschlossen.")

    current_capital = start_capital
    peak_capital = start_capital
    max_drawdown_pct = 0.0
    trades_count = 0
    wins_count = 0
    position = None

    iterator = tqdm(data.iterrows(), total=len(data), desc="Backtesting") if verbose else data.iterrows()

    for timestamp, current_candle in iterator:
        if position:
            exit_price = None
            if position['side'] == 'long':
                if not position['trailing_active'] and current_candle['high'] >= position['activation_price']: position['trailing_active'] = True
                if position['trailing_active']:
                    position['peak_price'] = max(position['peak_price'], current_candle['high'])
                    trailing_sl = position['peak_price'] * (1 - callback_rate)
                    position['stop_loss'] = max(position['stop_loss'], trailing_sl)
                if current_candle['low'] <= position['stop_loss']: exit_price = position['stop_loss']
                elif not position['trailing_active'] and current_candle['high'] >= position['take_profit']: exit_price = position['take_profit']
            elif position['side'] == 'short':
                if not position['trailing_active'] and current_candle['low'] <= position['activation_price']: position['trailing_active'] = True
                if position['trailing_active']:
                    position['peak_price'] = min(position['peak_price'], current_candle['low'])
                    trailing_sl = position['peak_price'] * (1 + callback_rate)
                    position['stop_loss'] = min(position['stop_loss'], trailing_sl)
                if current_candle['high'] >= position['stop_loss']: exit_price = position['stop_loss']
                elif not position['trailing_active'] and current_candle['low'] <= position['take_profit']: exit_price = position['take_profit']

            if exit_price:
                pnl_pct = (exit_price / position['entry_price'] - 1) if position['side'] == 'long' else (1 - exit_price / position['entry_price'])
                notional_value = position['margin_used'] * leverage
                pnl_usd = notional_value * pnl_pct
                total_fees = notional_value * fee_pct * 2
                capital_before_close = current_capital # Debug
                current_capital += (pnl_usd - total_fees)
                
                # if abs(pnl_usd) > capital_before_close * 10: # DEBUG
                #     print(f"\nDEBUG EXIT: Time={timestamp}, Side={position['side']}, Entry={position['entry_price']:.2f}, Exit={exit_price:.2f}")
                #     print(f"  -> Notional={notional_value:.2f}, Margin={position['margin_used']:.2f}, Lev={leverage}")
                #     print(f"  -> PnL %={pnl_pct*100:.2f}%, PnL $={pnl_usd:.2f}, Fees $={total_fees:.2f}")
                #     print(f"  -> Capital: {capital_before_close:.2f} -> {current_capital:.2f}")

                if (pnl_usd - total_fees) > 0: wins_count += 1
                trades_count += 1
                position = None
                peak_capital = max(peak_capital, current_capital)
                drawdown = (peak_capital - current_capital) / peak_capital if peak_capital > 0 else 0
                max_drawdown_pct = max(max_drawdown_pct, drawdown)
                if current_capital <= 0: break

        if not position and current_capital > 0:
            side, _ = get_titan_signal(smc_results, current_candle, params={})

            if side:
                entry_price = current_candle['close']
                current_atr = current_candle.get('atr')
                if pd.isna(current_atr) or current_atr <= 0: continue

                sl_distance = current_atr * atr_multiplier_sl
                if sl_distance <= 0: continue

                risk_amount_usd = current_capital * risk_per_trade_pct
                sl_distance_pct_equivalent = sl_distance / entry_price
                if sl_distance_pct_equivalent <= 1e-6: continue

                notional_value = risk_amount_usd / sl_distance_pct_equivalent
                margin_used = notional_value / leverage

                # if notional_value > current_capital * leverage * 2: # DEBUG
                #      print(f"\nDEBUG ENTRY: Time={timestamp}, Side={side}, Entry={entry_price:.2f}")
                #      print(f"  -> ATR={current_atr:.4f}, SL_Dist $={sl_distance:.2f}, SL_Dist %={sl_distance_pct_equivalent*100:.4f}%")
                #      print(f"  -> Risk $={risk_amount_usd:.2f}, Notional={notional_value:.2f}, Margin={margin_used:.2f}, Lev={leverage}")
                #      print(f"  -> Current Capital: {current_capital:.2f}")

                if margin_used > current_capital: continue

                stop_loss = entry_price - sl_distance if side == 'buy' else entry_price + sl_distance
                take_profit = entry_price + sl_distance * risk_reward_ratio if side == 'buy' else entry_price - sl_distance * risk_reward_ratio
                activation_price = entry_price + sl_distance * activation_rr if side == 'buy' else entry_price - sl_distance * activation_rr

                position = {
                    'side': 'long' if side == 'buy' else 'short',
                    'entry_price': entry_price, 'stop_loss': stop_loss,
                    'take_profit': take_profit, 'margin_used': margin_used,
                    'trailing_active': False, 'activation_price': activation_price,
                    'peak_price': entry_price
                }

    win_rate = (wins_count / trades_count * 100) if trades_count > 0 else 0
    final_pnl_pct = ((current_capital - start_capital) / start_capital) * 100 if start_capital > 0 else 0
    final_capital = max(0, current_capital)

    return {
        "total_pnl_pct": final_pnl_pct, "trades_count": trades_count,
        "win_rate": win_rate, "max_drawdown_pct": max_drawdown_pct,
        "end_capital": final_capital
    }
