# /root/titanbot/src/titanbot/analysis/backtester.py (Mit DYNAMISCHER Margin/Risiko vom CURRENT Capital und MTF-Bias)
import bisect
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
from titanbot.strategy.trade_logic import get_titan_signal

secrets_cache = None

_TF_SECONDS = {300: '5m', 900: '15m', 1800: '30m', 3600: '1h',
               7200: '2h', 14400: '4h', 21600: '6h', 86400: '1d'}

def _infer_timeframe(idx):
    if len(idx) < 2:
        return None
    delta = int((idx[1] - idx[0]).total_seconds())
    return _TF_SECONDS.get(delta)

_HTF_MAP = {
    '5m': '1h', '15m': '1h', '30m': '4h', '1h': '4h',
    '2h': '1d', '4h': '1d', '6h': '1d', '1d': None
}
_PD_RESAMPLE = {'1h': '1h', '4h': '4h', '1d': '1D'}

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


def run_smc_backtest(data, smc_params, risk_params, start_capital=1000, verbose=False, bar_index_offset=0):
    if data.empty or len(data) < 15:
        return {"total_pnl_pct": -100, "trades_count": 0, "win_rate": 0, "max_drawdown_pct": 1.0, "end_capital": start_capital}

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
    
    # --- Parameter und SMC-Engine Setup ---
    risk_reward_ratio = risk_params.get('risk_reward_ratio', 1.5)
    risk_per_trade_pct = risk_params.get('risk_per_trade_pct', 1.0) / 100
    activation_rr = risk_params.get('trailing_stop_activation_rr', 2.0)
    callback_rate = risk_params.get('trailing_stop_callback_rate_pct', 1.0) / 100
    max_leverage = risk_params.get('max_leverage', 20)
    min_leverage = risk_params.get('min_leverage', 3)
    # SL-Parameter wie Live-Bot
    atr_multiplier_sl = risk_params.get('atr_multiplier_sl', 2.0)
    min_sl_pct = risk_params.get('min_sl_pct', 0.5) / 100.0
    structure_sl_buffer_pct = risk_params.get('structure_sl_buffer_pct', 0.2) / 100.0
    fee_pct = 0.05 / 100

    absolute_max_notional_value = 1000000

    # SMC-Engine — nutze vorberechnete Ergebnisse wenn vorhanden (Optimizer-Cache)
    precomputed = smc_params.get('_precomputed_smc')
    if precomputed is not None:
        smc_results   = precomputed['smc_results']
        smc_structures = precomputed['smc_structures']
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

    # --- HTF Bias Vorberechnung (kein Look-Ahead: bisect auf HTF-Open-Zeiten) ---
    use_mtf_filter = smc_params.get('use_mtf_filter', False)
    htf_bias_times  = []  # list[pd.Timestamp]
    htf_bias_values = []  # list[Bias]
    if use_mtf_filter:
        _tf = smc_params.get('_timeframe') or _infer_timeframe(data.index)
        _htf_rule_key = _HTF_MAP.get(_tf)
        _pd_rule = _PD_RESAMPLE.get(_htf_rule_key) if _htf_rule_key else None
        if _pd_rule:
            try:
                htf_data = data[['open', 'high', 'low', 'close']].resample(_pd_rule).agg(
                    {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
                ).dropna()
                if len(htf_data) >= 20:
                    _htf_engine = SMCEngine(settings={'swingsLength': 10, 'closeTrails': False})
                    _htf_results = _htf_engine.process_dataframe(htf_data.copy())
                    _htf_states = _htf_results.get('bar_states', [])
                    for _ts, _st in zip(htf_data.index, _htf_states):
                        _bs = _st.get('swing_bias', '')
                        _bias = (Bias.BULLISH if _bs == 'bullish'
                                 else Bias.BEARISH if _bs == 'bearish'
                                 else Bias.NEUTRAL)
                        htf_bias_times.append(_ts)
                        htf_bias_values.append(_bias)
            except Exception as _e:
                print(f"WARNUNG: HTF Bias Vorberechnung fehlgeschlagen: {_e}")

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

        # Mark-to-Market: unrealisierter P&L der offenen Position
        unrealized_pnl = 0.0
        if position:
            pnl_mult = 1 if position['side'] == 'long' else -1
            unrealized_pnl = position['notional_value'] * (current_candle['close'] / position['entry_price'] - 1) * pnl_mult

        mtm_equity = current_capital + unrealized_pnl

        # Liquidation: wenn mark-to-market Kapital ≤ 0 → Position wird zwangsgeschlossen
        if position and mtm_equity <= 0:
            trades_count += 1  # als verlorenen Trade zählen
            current_capital = 0
            equity_curve.append({'timestamp': timestamp, 'equity': 0.0})
            max_drawdown_pct = 1.0  # 100% DD
            position = None
            break

        equity_curve.append({'timestamp': timestamp, 'equity': mtm_equity})

        # Drawdown jede Kerze (mark-to-market), nicht nur bei Trade-Schluß
        peak_capital = max(peak_capital, mtm_equity)
        if peak_capital > 0:
            drawdown = (peak_capital - mtm_equity) / peak_capital
            max_drawdown_pct = max(max_drawdown_pct, drawdown)

        # --- Positions-Management ---
        closed_this_bar = False
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
                closed_this_bar = True

        # --- Einstiegs-Logik (max. 1 Trade pro Kerze) ---
        if not position and not closed_this_bar and current_capital > 0:
            prev_candle = data.iloc[i-1] if i > 0 else None

            # Per-Bar HTF Bias: letzter abgeschlossener HTF-Balken (bisect - 2)
            market_bias = Bias.NEUTRAL
            if use_mtf_filter and htf_bias_times:
                _pos = bisect.bisect_right(htf_bias_times, timestamp) - 2
                if _pos >= 0:
                    market_bias = htf_bias_values[_pos]

            # Per-Bar gefilterte SMC-Strukturen (kein Look-Ahead-Bias):
            # Nur OBs/FVGs die zum Zeitpunkt von Bar i bereits gebildet wurden,
            # noch nicht mitigiert waren und innerhalb des Live-Bot-Fensters
            # (letzte 300 Kerzen) liegen — identisch zu fetch_recent_ohlcv(limit=300).
            _smc_window = smc_params.get('smc_lookback', 300)
            _window_start = i - _smc_window
            _all_int_obs   = smc_results.get('all_internal_obs', [])
            _all_swing_obs = smc_results.get('all_swing_obs', [])
            _all_fvgs      = smc_results.get('all_fvgs', [])
            bar_smc = {
                'unmitigated_internal_obs': [
                    ob for ob in _all_int_obs
                    if _window_start <= ob.bar_index <= i and (ob.mitigated_bar == -1 or ob.mitigated_bar > i)
                ],
                'unmitigated_swing_obs': [
                    ob for ob in _all_swing_obs
                    if _window_start <= ob.bar_index <= i and (ob.mitigated_bar == -1 or ob.mitigated_bar > i)
                ],
                'unmitigated_fvgs': [
                    fvg for fvg in _all_fvgs
                    if _window_start <= fvg.start_bar_index <= i and (fvg.mitigated_bar == -1 or fvg.mitigated_bar > i)
                ],
                'liquidity_levels': smc_results.get('liquidity_levels', []),
                'enriched_df': smc_results.get('enriched_df'),
            }

            side, _, signal_context = get_titan_signal(bar_smc, current_candle, params=params_for_logic, market_bias=market_bias, prev_candle=prev_candle)

            if side:
                entry_price = current_candle['close']
                current_atr = current_candle.get('atr')
                if pd.isna(current_atr) or current_atr <= 0: continue

                # --- SL: Struktur-basiert (wie Live-Bot), dann ATR-Fallback ---
                sl_distance = None
                if signal_context:
                    level_low  = signal_context.get('level_low')
                    level_high = signal_context.get('level_high')
                    if side == 'buy' and level_low:
                        sl_price = level_low - entry_price * structure_sl_buffer_pct
                        if sl_price < entry_price:
                            sl_distance = entry_price - sl_price
                    elif side == 'sell' and level_high:
                        sl_price = level_high + entry_price * structure_sl_buffer_pct
                        if sl_price > entry_price:
                            sl_distance = sl_price - entry_price

                if not sl_distance or sl_distance <= 0:
                    sl_distance = max(current_atr * atr_multiplier_sl, entry_price * min_sl_pct)

                # Sicherheits-Minimum: mind. 0.1% Abstand
                sl_distance = max(sl_distance, entry_price * 0.001)
                if sl_distance <= 0: continue

                # --- TP: Einfaches R:R (wie Live-Bot) ---
                if side == 'buy':
                    stop_loss   = entry_price - sl_distance
                    take_profit = entry_price + sl_distance * risk_reward_ratio
                else:
                    stop_loss   = entry_price + sl_distance
                    take_profit = entry_price - sl_distance * risk_reward_ratio

                # --- Variabler Hebel: Risk-basiertes Position Sizing ---
                risk_amount_usd = current_capital * risk_per_trade_pct
                sl_pct = sl_distance / entry_price
                if sl_pct <= 1e-6: continue

                target_notional = risk_amount_usd / sl_pct
                MIN_NOTIONAL_USDT = 5.0
                if target_notional < MIN_NOTIONAL_USDT:
                    target_notional = MIN_NOTIONAL_USDT
                eff_leverage = target_notional / current_capital
                eff_leverage = max(min_leverage, min(eff_leverage, max_leverage))
                eff_leverage = max(1, math.floor(eff_leverage))
                final_notional_value = min(target_notional, absolute_max_notional_value)
                if final_notional_value < MIN_NOTIONAL_USDT: continue

                margin_used = final_notional_value / eff_leverage
                activation_price = entry_price + sl_distance * activation_rr if side == 'buy' else entry_price - sl_distance * activation_rr

                position = {
                    'side': 'long' if side == 'buy' else 'short',
                    'entry_price': entry_price, 'stop_loss': stop_loss,
                    'take_profit': take_profit, 'margin_used': margin_used,
                    'notional_value': final_notional_value,
                    'trailing_active': False, 'activation_price': activation_price,
                    'peak_price': entry_price,
                    'entry_time': timestamp
                }

    # --- Offene Position am Backtest-Ende schließen (letzter bekannter Schlusskurs) ---
    if position and len(data) > 0:
        last_candle = data.iloc[-1]
        last_price = last_candle['close']
        pnl_pct = (last_price / position['entry_price'] - 1) if position['side'] == 'long' else (1 - last_price / position['entry_price'])
        pnl_usd = position['notional_value'] * pnl_pct
        total_fees = position['notional_value'] * fee_pct * 2
        net_pnl = pnl_usd - total_fees
        current_capital += net_pnl
        if current_capital <= 0:
            current_capital = 0
        else:
            if net_pnl > 0:
                wins_count += 1
        trades_count += 1
        trades_list.append({
            'entry_' + position['side']: {
                'time': position['entry_time'].isoformat() if hasattr(position.get('entry_time'), 'isoformat') else str(position.get('entry_time')),
                'price': position['entry_price']
            },
            'exit_' + position['side']: {
                'time': 'Backtest-Ende',
                'price': last_price
            },
            'stop_loss':   position['stop_loss'],
            'take_profit': position['take_profit'],
            'entry_time':  position['entry_time'],
            'exit_time':   'Backtest-Ende',
        })
        # Equity-Kurve mit finalem realisierten Wert aktualisieren
        mtm_equity = max(0.0, current_capital)
        equity_curve.append({'timestamp': data.index[-1], 'equity': mtm_equity})
        peak_capital = max(peak_capital, mtm_equity)
        if peak_capital > 0:
            drawdown = (peak_capital - mtm_equity) / peak_capital
            max_drawdown_pct = max(max_drawdown_pct, drawdown)
        position = None

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
