# /root/titanbot/src/titanbot/analysis/backtester.py (Mit DYNAMISCHER Margin/Risiko vom CURRENT Capital und MTF-Bias)
import os
import pandas as pd
import numpy as np
import json
import sys
from tqdm import tqdm
import ta
import math

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from titanbot.utils.exchange import Exchange
from titanbot.strategy.smc_engine import SMCEngine, Bias
from titanbot.strategy.trade_logic import get_titan_signal, get_zone_based_tp
from titanbot.utils.timeframe_utils import determine_htf # NEU: Import determine_htf

secrets_cache = None

# --- load_data Funktion bleibt unverändert ---
def load_data(symbol, timeframe, start_date_str, end_date_str):
    global secrets_cache
    data_dir = os.path.join(PROJECT_ROOT, 'data')
    cache_dir = os.path.join(data_dir, 'cache')
    symbol_filename = symbol.replace('/', '-').replace(':', '-')
    cache_file = os.path.join(cache_dir, f"{symbol_filename}_{timeframe}.csv")
    try:
        if not os.path.exists(data_dir): os.makedirs(data_dir); print(f"Info: Verzeichnis '{data_dir}' erstellt.")
        os.makedirs(cache_dir, exist_ok=True)
    except OSError as e: print(f"FATAL: Konnte Cache-Verzeichnis '{cache_dir}' nicht erstellen: {e}"); return pd.DataFrame()

    if os.path.exists(cache_file):
        try:
            data = pd.read_csv(cache_file, index_col='timestamp', parse_dates=True)
            data_start = data.index.min(); data_end = data.index.max()
            req_start = pd.to_datetime(start_date_str, utc=True); req_end = pd.to_datetime(end_date_str, utc=True)
            if data_start <= req_start and data_end >= req_end:
                return data.loc[req_start:req_end]
            else: print(f"Info: Cache für {symbol} {timeframe} nicht aktuell/vollständig. Lade neu.")
        except Exception as e:
            print(f"WARNUNG: Fehler beim Lesen der Cache-Datei '{cache_file}': {e}. Lade neu.");
            try: os.remove(cache_file)
            except OSError: pass

    print(f"Starte Download für {symbol} ({timeframe}) von der Börse...")
    try:
        if secrets_cache is None:
            with open(os.path.join(PROJECT_ROOT, 'secret.json'), "r") as f: secrets_cache = json.load(f)
        if 'titanbot' not in secrets_cache or not isinstance(secrets_cache['titanbot'], list) or not secrets_cache['titanbot']:
            print("FEHLER: 'titanbot' Schlüssel in secret.json fehlt/leer."); return pd.DataFrame()
        api_setup = secrets_cache['titanbot'][0]
        exchange = Exchange(api_setup)
        if not exchange.markets:
            print("FEHLER: Exchange konnte nicht initialisiert werden."); return pd.DataFrame()
        full_data = exchange.fetch_historical_ohlcv(symbol, timeframe, start_date_str, end_date_str)
        if not full_data.empty:
            try:
                full_data.to_csv(cache_file);
                req_start_dt = pd.to_datetime(start_date_str, utc=True); req_end_dt = pd.to_datetime(end_date_str, utc=True)
                return full_data.loc[req_start_dt:req_end_dt]
            except Exception as e_save:
                print(f"FEHLER beim Speichern der Cache-Datei '{cache_file}': {e_save}")
                req_start_dt = pd.to_datetime(start_date_str, utc=True); req_end_dt = pd.to_datetime(end_date_str, utc=True)
                return full_data.loc[req_start_dt:req_end_dt]
        else: return pd.DataFrame()
    except FileNotFoundError: print(f"FEHLER: secret.json nicht gefunden."); return pd.DataFrame()
    except KeyError: print("FEHLER: API-Keys in secret.json nicht gefunden."); return pd.DataFrame()
    except Exception as e: print(f"FEHLER beim Daten-Download: {e}"); import traceback; traceback.print_exc(); return pd.DataFrame()


def run_smc_backtest(data, smc_params, risk_params, start_capital=1000, verbose=False):
    if data.empty or len(data) < 15:
        return {"total_pnl_pct": -100, "trades_count": 0, "win_rate": 0, "max_drawdown_pct": 1.0, "end_capital": start_capital}

    symbol = smc_params.get('symbol', '') # Holen Sie Symbol und Timeframe für MTF-Check
    timeframe = smc_params.get('timeframe', '')
    htf = smc_params.get('htf') # MTF-Timeframe aus Parametern

    # --- MTF-Bias vorbereiten ---
    # htf_bias kann direkt übergeben werden (pre-computed, einmal pro Optimierung)
    # → spart SMCEngine.process_dataframe() auf HTF-Daten bei jedem Trial
    precomputed_bias = smc_params.get('htf_bias')
    if precomputed_bias is not None:
        market_bias = precomputed_bias
    else:
        market_bias = Bias.NEUTRAL
        if htf and htf != timeframe:
            htf_data = smc_params.get('htf_data')
            if htf_data is None:
                if verbose: print(f"MTF-Check: Lade Daten für HTF ({htf})...")
                htf_data = load_data(symbol, htf, data.index.min().strftime('%Y-%m-%d'), data.index.max().strftime('%Y-%m-%d'))
            if htf_data.empty:
                if verbose: print("MTF-Check: Konnte HTF-Daten nicht laden. Verwende Bias.NEUTRAL.")
            else:
                htf_engine = SMCEngine(settings={'swingsLength': 50, 'ob_mitigation': 'Close'})
                htf_engine.process_dataframe(htf_data[['open', 'high', 'low', 'close']].copy())
                market_bias = htf_engine.swingTrend
                if verbose: print(f"MTF-Check: HTF-Swing-Bias ({htf}): {market_bias.name}")
    # --- ENDE MTF ---

    # --- Indikator-Berechnungen ---
    # Wenn ATR/ADX/volume_ma schon vorberechnet in den Daten (vom Optimizer),
    # überspringen wir die teure Neuberechnung pro Trial.
    adx_period = smc_params.get('adx_period', 14)
    volume_ma_period = smc_params.get('volume_ma_period', 20)

    try:
        if 'atr' not in data.columns or data['atr'].isna().all():
            atr_indicator = ta.volatility.AverageTrueRange(high=data['high'], low=data['low'], close=data['close'], window=14)
            data['atr'] = atr_indicator.average_true_range()

        if 'adx' not in data.columns or data['adx'].isna().all():
            adx_indicator = ta.trend.ADXIndicator(high=data['high'], low=data['low'], close=data['close'], window=adx_period)
            data['adx'] = adx_indicator.adx()
            data['adx_pos'] = adx_indicator.adx_pos()
            data['adx_neg'] = adx_indicator.adx_neg()

        if 'volume_ma' not in data.columns or data['volume_ma'].isna().all():
            data['volume_ma'] = data['volume'].rolling(window=volume_ma_period).mean()

        data.dropna(subset=['atr', 'adx'], inplace=True)

        if data.empty:
            return {"total_pnl_pct": -100, "trades_count": 0, "win_rate": 0, "max_drawdown_pct": 1.0, "end_capital": start_capital}
    except Exception as e:
        print(f"FEHLER bei Indikator-Berechnung: {e}")
        return {"total_pnl_pct": -999, "trades_count": 0, "win_rate": 0, "max_drawdown_pct": 1.0, "end_capital": start_capital}
    
    # --- Parameter und SMC-Engine Setup (Unverändert) ---
    risk_reward_ratio = risk_params.get('risk_reward_ratio', 1.5)
    risk_per_trade_pct = risk_params.get('risk_per_trade_pct', 1.0) / 100
    activation_rr = risk_params.get('trailing_stop_activation_rr', 2.0)
    callback_rate = risk_params.get('trailing_stop_callback_rate_pct', 1.0) / 100
    max_leverage = risk_params.get('max_leverage', 20)
    min_leverage = risk_params.get('min_leverage', 3)
    sl_buffer_atr_mult = risk_params.get('sl_buffer_atr_mult', 0.2)
    fee_pct = 0.05 / 100

    absolute_max_notional_value = 1000000

    # SMC-Engine — nutze vorberechnete Ergebnisse wenn vorhanden (Optimizer-Cache)
    precomputed = smc_params.get('_precomputed_smc')
    if precomputed is not None:
        smc_results   = precomputed['smc_results']
        smc_structures = precomputed['smc_structures']
        engine = None
    else:
        engine = SMCEngine(settings=smc_params)
        smc_results = engine.process_dataframe(data[['open', 'high', 'low', 'close']].copy())
        smc_structures = {
            'order_blocks': engine.swingOrderBlocks + engine.internalOrderBlocks,
            'fair_value_gaps': engine.fairValueGaps,
            'events': engine.event_log,
            'data_times': engine.times,
        }

    # SMC-Spalten (P/D, Sweep-State) in Haupt-Dataframe übertragen
    enriched_df = smc_results.get('enriched_df')
    if enriched_df is not None:
        for col in enriched_df.columns:
            if col.startswith('smc_'):
                data[col] = enriched_df[col].values

    current_capital = start_capital
    peak_capital = start_capital
    max_drawdown_pct = 0.0
    trades_count = 0
    wins_count = 0
    position = None
    
    # NEU: Trade-Liste und Equity-Curve für Visualisierung
    trades_list = []
    equity_curve = []

    params_for_logic = {"strategy": smc_params, "risk": risk_params}

    # --- Backtest Loop ---
    for i, (timestamp, current_candle) in enumerate(data.iterrows()):
        if current_capital <= 0: break
        
        # NEU: Equity-Punkt für jeden Timestamp speichern
        equity_curve.append({'timestamp': timestamp, 'equity': current_capital})

        # --- NEU: Dynamische MTF-Bias-Aktualisierung (falls nötig) ---
        # Diese Simulation ist vereinfacht und geht davon aus, 
        # dass die Struktur auf dem HTF stabil bleibt, 
        # da der SMC-Check nur einmal pro HTF-Kerze laufen würde.
        # Im Live-Bot wird der Bias vor JEDEM Lauf neu geprüft, 
        # hier verwenden wir den initial berechneten Bias.

        # --- Positions-Management (Unverändert) ---
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
                notional_value = position['notional_value']
                pnl_usd = notional_value * pnl_pct
                total_fees = notional_value * fee_pct * 2
                current_capital += (pnl_usd - total_fees)
                if current_capital <= 0: current_capital = 0; break
                if (pnl_usd - total_fees) > 0: wins_count += 1
                trades_count += 1
                
                # NEU: Trade für Visualisierung speichern
                trade_record = {
                    'entry_' + position['side']: {
                        'time': position['entry_time'].isoformat() if hasattr(position.get('entry_time'), 'isoformat') else str(position.get('entry_time')),
                        'price': position['entry_price']
                    },
                    'exit_' + position['side']: {
                        'time': timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp),
                        'price': exit_price
                    },
                    'stop_loss':   position['stop_loss'],
                    'take_profit': position['take_profit'],
                    'entry_time':  position['entry_time'],
                    'exit_time':   timestamp,
                }
                trades_list.append(trade_record)
                
                position = None
                peak_capital = max(peak_capital, current_capital)
                if peak_capital > 0:
                    drawdown = (peak_capital - current_capital) / peak_capital
                    max_drawdown_pct = max(max_drawdown_pct, drawdown)

        # --- Einstiegs-Logik ---
        if not position and current_capital > 0:
            # GEÄNDERT: market_bias an die Signalfunktion übergeben, signal_context empfangen
            # Hole auch die vorherige Kerze für Confirmation-Logik (falls nötig)
            prev_candle = data.iloc[i-1] if i > 0 else None
            side, _, signal_context = get_titan_signal(smc_results, current_candle, params=params_for_logic, market_bias=market_bias, prev_candle=prev_candle) 

            if side:
                entry_price = current_candle['close']
                current_atr = current_candle.get('atr')
                if pd.isna(current_atr) or current_atr <= 0: continue

                # --- SMC-Zonenbasierter SL (hinter die Zone) ---
                buffer = current_atr * sl_buffer_atr_mult
                zone_low = signal_context.get('level_low', entry_price)
                zone_high = signal_context.get('level_high', entry_price)
                if side == 'buy':
                    stop_loss = zone_low - buffer
                else:
                    stop_loss = zone_high + buffer

                sl_distance = abs(entry_price - stop_loss)
                # Sicherheits-Minimum: mind. 0.1% Abstand
                sl_distance = max(sl_distance, entry_price * 0.001)
                if sl_distance <= 0: continue

                # --- Variabler Hebel: Risk-basiertes Position Sizing ---
                risk_amount_usd = current_capital * risk_per_trade_pct
                sl_pct = sl_distance / entry_price
                if sl_pct <= 1e-6: continue

                target_notional = risk_amount_usd / sl_pct
                # Hebel klemmen: min_leverage ≤ eff_leverage ≤ max_leverage
                eff_leverage = target_notional / current_capital
                eff_leverage = max(min_leverage, min(eff_leverage, max_leverage))
                final_notional_value = min(current_capital * eff_leverage, absolute_max_notional_value)
                if final_notional_value < 1.0: continue

                # Backtester: Single-Position — margin check entfällt (Floating-Point-Bug vermieden)
                margin_used = round(final_notional_value / eff_leverage, 2)

                take_profit = get_zone_based_tp(side, entry_price, sl_distance, risk_reward_ratio, smc_results, i)
                activation_price = entry_price + sl_distance * activation_rr if side == 'buy' else entry_price - sl_distance * activation_rr

                position = {
                    'side': 'long' if side == 'buy' else 'short',
                    'entry_price': entry_price, 'stop_loss': stop_loss,
                    'take_profit': take_profit, 'margin_used': margin_used,
                    'notional_value': final_notional_value,
                    'trailing_active': False, 'activation_price': activation_price,
                    'peak_price': entry_price,
                    'entry_time': timestamp  # NEU: Entry-Zeit speichern
                }

    # --- Endergebnis ---
    win_rate = (wins_count / trades_count * 100) if trades_count > 0 else 0
    final_pnl_pct = ((current_capital - start_capital) / start_capital) * 100 if start_capital > 0 else 0
    final_capital = max(0, current_capital)

    return {
        "total_pnl_pct": final_pnl_pct, "trades_count": trades_count,
        "win_rate": win_rate, "max_drawdown_pct": max_drawdown_pct,
        "end_capital": final_capital,
        "trades_list": trades_list,  # NEU: Für Visualisierung
        "equity_curve": equity_curve,  # NEU: Für Visualisierung
        "smc_structures": smc_structures  # NEU: OBs, FVGs, Events für Chart
    }
