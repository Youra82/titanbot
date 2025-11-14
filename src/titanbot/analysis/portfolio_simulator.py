# /root/titanbot/src/titanbot/analysis/portfolio_simulator.py (Version für TitanBot SMC - KORRIGIERT mit MTF-Bias)
import pandas as pd
import numpy as np
from tqdm import tqdm
import sys
import os
import ta # Import für ATR/ADX hinzugefügt
import math # Import für math.ceil
import json

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from titanbot.strategy.smc_engine import SMCEngine, Bias
from titanbot.strategy.trade_logic import get_titan_signal # Nutzt die Live-Logik
from titanbot.analysis.backtester import load_data # Importiere load_data für HTF-Daten
from titanbot.utils.timeframe_utils import determine_htf # NEU: Import für determine_htf

def run_portfolio_simulation(start_capital, strategies_data, start_date, end_date):
    """
    Führt eine chronologische Portfolio-Simulation mit mehreren SMC-Strategien durch.
    Beinhaltet MTF-Bias-Check.
    """
    print("\n--- Starte Portfolio-Simulation (SMC)... ---")

    # --- 0. MTF-Bias für jede Strategie bestimmen ---
    # Da der Simulator nur die Daten lädt, müssen wir den HTF-Bias hier bestimmen
    mtf_bias_by_strategy = {}
    print("0/4: Bestimme MTF-Bias für jede Strategie...")
    
    for key, strat in tqdm(strategies_data.items(), desc="MTF Bias Check"):
        symbol = strat['symbol']
        timeframe = strat['timeframe']
        
        # NEU: Hole HTF aus der Konfiguration (wird von show_results übergeben)
        htf = strat.get('htf')
        if not htf:
            # Fallback, falls Konfigurationsdatei veraltet war (sollte nicht passieren)
            htf = determine_htf(timeframe)
            strat['htf'] = htf

        market_bias = Bias.NEUTRAL
        if htf and htf != timeframe:
            # Lade HTF-Daten für den gesamten Backtest-Zeitraum
            htf_data = load_data(symbol, htf, start_date, end_date)
            
            if htf_data.empty or len(htf_data) < 150:
                # print(f"MTF-Check: Nicht genügend HTF-Daten für {key}.")
                pass # Bleibt bei Bias.NEUTRAL
            else:
                # Führe SMC-Analyse auf HTF-Daten durch (Standard SMC settings)
                htf_engine = SMCEngine(settings={'swingsLength': 50, 'ob_mitigation': 'Close'}) 
                htf_engine.process_dataframe(htf_data[['open', 'high', 'low', 'close']].copy())
                market_bias = htf_engine.swingTrend
        
        mtf_bias_by_strategy[key] = market_bias
        
    # --- ENDE MTF-Bias Bestimmung ---

    # --- 1. Kombiniere alle Zeitstempel & berechne Indikatoren ---
    all_timestamps = set()
    print("1/4: Berechne Indikatoren (ATR/ADX) für alle Strategien...")
    data_with_indicators = {} 

    for key, strat in strategies_data.items():
        if 'data' in strat and not strat['data'].empty:
            
            try:
                temp_data = strat['data'].copy()
                smc_params = strat.get('smc_params', {})
                adx_period = smc_params.get('adx_period', 14) 

                if len(temp_data) >= 15:
                    # ATR
                    atr_indicator = ta.volatility.AverageTrueRange(high=temp_data['high'], low=temp_data['low'], close=temp_data['close'], window=14)
                    temp_data['atr'] = atr_indicator.average_true_range()

                    # ADX
                    adx_indicator = ta.trend.ADXIndicator(high=temp_data['high'], low=temp_data['low'], close=temp_data['close'], window=adx_period)
                    temp_data['adx'] = adx_indicator.adx()
                    temp_data['adx_pos'] = adx_indicator.adx_pos()
                    temp_data['adx_neg'] = adx_indicator.adx_neg()
                    temp_data.dropna(subset=['atr', 'adx'], inplace=True) 

                    if not temp_data.empty:
                        data_with_indicators[key] = temp_data
                        all_timestamps.update(temp_data.index)
                    else:
                        print(f"WARNUNG: Keine Daten für Strategie {key} nach Indikator-Berechnung übrig.")
                else:
                    print(f"WARNUNG: Nicht genug Daten ({len(temp_data)}) für Indikatoren bei Strategie {key}.")
            except Exception as e:
                print(f"FEHLER bei Indikator-Berechnung für {key}: {e}")
        else:
            print(f"WARNUNG: Keine Daten für Strategie {key} gefunden.")

    # Ersetze Originaldaten durch Daten mit Indikatoren
    strategies_data_processed = {}
    for key, strat in strategies_data.items():
        if key in data_with_indicators:
            strategies_data_processed[key] = strat.copy()
            strategies_data_processed[key]['data'] = data_with_indicators[key]

    if not all_timestamps or not strategies_data_processed:
        print("Keine gültigen Daten für die Simulation gefunden (oder Indikatoren konnten nicht berechnet werden).")
        return None

    sorted_timestamps = sorted(list(all_timestamps))
    print(f"-> {len(sorted_timestamps)} eindeutige Zeitstempel gefunden.")

    # --- 2. SMC-Analyse für jede Strategie ---
    print("2/4: Führe SMC-Analyse für alle gültigen Strategien durch...")
    smc_results_by_strategy = {}
    valid_strategies = {}

    for key, strat in tqdm(strategies_data_processed.items(), desc="SMC Analyse"):
        try:
            # Stelle sicher, dass Symbol/Timeframe im smc_params ist, falls im backtester benötigt
            strat['smc_params']['symbol'] = strat['symbol']
            strat['smc_params']['timeframe'] = strat['timeframe']
            strat['smc_params']['htf'] = strat['htf'] # HTF hinzufügen

            engine = SMCEngine(settings=strat.get('smc_params', {}))
            smc_results_by_strategy[key] = engine.process_dataframe(strat['data'][['open','high','low','close']].copy())
            valid_strategies[key] = strat
        except Exception as e:
            print(f"FEHLER bei SMC-Analyse für {key}: {e}")

    if not valid_strategies:
        print("Für keine Strategie konnte die SMC-Analyse erfolgreich durchgeführt werden.")
        return None

    # --- 3. Chronologische Simulation ---
    print("3/4: Führe chronologische Backtests durch...")
    equity = start_capital
    peak_equity = start_capital
    max_drawdown_pct = 0.0
    max_drawdown_date = None
    min_equity_ever = start_capital
    liquidation_date = None

    open_positions = {}
    trade_history = []
    equity_curve = []

    # Konstanten aus Backtester
    fee_pct = 0.05 / 100
    max_allowed_effective_leverage = 10
    absolute_max_notional_value = 1000000
    min_notional = 5.0
    
    for ts in tqdm(sorted_timestamps, desc="Simuliere Portfolio"):
        if liquidation_date: break

        current_total_equity = equity
        unrealized_pnl = 0

        # --- 3a. Offene Positionen managen (Unverändert) ---
        positions_to_close = []
        for key, pos in open_positions.items():
            strat_data = valid_strategies.get(key)
            if not strat_data or ts not in strat_data['data'].index:
                if pos.get('last_known_price'):
                    pnl_mult = 1 if pos['side'] == 'long' else -1
                    unrealized_pnl += pos['notional_value'] * (pos['last_known_price'] / pos['entry_price'] -1) * pnl_mult
                continue

            current_candle = strat_data['data'].loc[ts]
            pos['last_known_price'] = current_candle['close']
            exit_price = None

            if pos['side'] == 'long':
                if not pos['trailing_active'] and current_candle['high'] >= pos['activation_price']:
                    pos['trailing_active'] = True
                if pos['trailing_active']:
                    pos['peak_price'] = max(pos['peak_price'], current_candle['high'])
                    trailing_sl = pos['peak_price'] * (1 - pos['callback_rate'])
                    pos['stop_loss'] = max(pos['stop_loss'], trailing_sl)
                if current_candle['low'] <= pos['stop_loss']: exit_price = pos['stop_loss']
                elif not pos['trailing_active'] and current_candle['high'] >= pos['take_profit']: exit_price = pos['take_profit']
            else: # Short
                if not pos['trailing_active'] and current_candle['low'] <= pos['activation_price']:
                    pos['trailing_active'] = True
                if pos['trailing_active']:
                    pos['peak_price'] = min(pos['peak_price'], current_candle['low'])
                    trailing_sl = pos['peak_price'] * (1 + pos['callback_rate'])
                    pos['stop_loss'] = min(pos['stop_loss'], trailing_sl)
                if current_candle['high'] >= pos['stop_loss']: exit_price = pos['stop_loss']
                elif not pos['trailing_active'] and current_candle['low'] <= pos['take_profit']: exit_price = pos['take_profit']

            if exit_price:
                pnl_pct = (exit_price / pos['entry_price'] - 1) if pos['side'] == 'long' else (1 - exit_price / pos['entry_price'])
                pnl_usd = pos['notional_value'] * pnl_pct
                total_fees = pos['notional_value'] * fee_pct * 2
                equity += (pnl_usd - total_fees)
                trade_history.append({'strategy_key': key, 'symbol': strat_data['symbol'], 'pnl': (pnl_usd - total_fees)})
                positions_to_close.append(key)
            else:
                pnl_mult = 1 if pos['side'] == 'long' else -1
                unrealized_pnl += pos['notional_value'] * (current_candle['close'] / pos['entry_price'] -1) * pnl_mult

        for key in positions_to_close:
            del open_positions[key]

        # --- 3b. Neue Signale prüfen und Positionen eröffnen ---
        if equity > 0:
            for key, strat in valid_strategies.items():
                if key not in open_positions and ts in strat['data'].index:
                    current_candle = strat['data'].loc[ts]
                    smc_results = smc_results_by_strategy.get(key)
                    risk_params = strat.get('risk_params', {})
                    smc_params = strat.get('smc_params', {})
                    market_bias = mtf_bias_by_strategy.get(key, Bias.NEUTRAL) # MTF Bias holen

                    if not smc_results: continue

                    # --- NEU: Kombiniere Parameter für die Logik-Funktion ---
                    params_for_logic = {"strategy": smc_params, "risk": risk_params}
                    
                    # FEHLER BEHOBEN: market_bias an die Signalfunktion übergeben
                    side, _ = get_titan_signal(smc_results, current_candle, params=params_for_logic, market_bias=market_bias) 

                    if side:
                        entry_price = current_candle['close']
                        risk_per_trade_pct = risk_params.get('risk_per_trade_pct', 1.0) / 100
                        risk_reward_ratio = risk_params.get('risk_reward_ratio', 2.0)
                        leverage = risk_params.get('leverage', 10)
                        activation_rr = risk_params.get('trailing_stop_activation_rr', 2.0)
                        callback_rate = risk_params.get('trailing_stop_callback_rate_pct', 1.0) / 100

                        # --- NEU: Hole optimierte SL-Parameter ---
                        atr_multiplier_sl = risk_params.get('atr_multiplier_sl', 2.0)
                        min_sl_pct = risk_params.get('min_sl_pct', 0.5) / 100.0

                        current_atr = current_candle.get('atr')
                        if pd.isna(current_atr) or current_atr <= 0:
                            continue

                        sl_distance_atr = current_atr * atr_multiplier_sl
                        sl_distance_min = entry_price * min_sl_pct
                        sl_distance = max(sl_distance_atr, sl_distance_min)
                        if sl_distance <= 0:
                            continue

                        risk_amount_usd = equity * risk_per_trade_pct
                        sl_distance_pct_equivalent = sl_distance / entry_price
                        if sl_distance_pct_equivalent <= 1e-6:
                            continue

                        calculated_notional_value = risk_amount_usd / sl_distance_pct_equivalent
                        max_notional_by_leverage = equity * max_allowed_effective_leverage
                        final_notional_value = min(calculated_notional_value, max_notional_by_leverage, absolute_max_notional_value)

                        if final_notional_value < min_notional:
                            continue

                        margin_used = math.ceil((final_notional_value / leverage) * 100) / 100

                        current_total_margin = sum(p['margin_used'] for p in open_positions.values())
                        if current_total_margin + margin_used > equity:
                            continue

                        stop_loss = entry_price - sl_distance if side == 'buy' else entry_price + sl_distance
                        take_profit = entry_price + sl_distance * risk_reward_ratio if side == 'buy' else entry_price - sl_distance * risk_reward_ratio
                        activation_price = entry_price + sl_distance * activation_rr if side == 'buy' else entry_price - sl_distance * activation_rr

                        open_positions[key] = {
                            'side': 'long' if side == 'buy' else 'short',
                            'entry_price': entry_price,
                            'stop_loss': stop_loss,
                            'take_profit': take_profit,
                            'notional_value': final_notional_value,
                            'margin_used': margin_used,
                            'trailing_active': False,
                            'activation_price': activation_price,
                            'peak_price': entry_price,
                            'callback_rate': callback_rate,
                            'last_known_price': entry_price
                            }

        # --- 3c. Equity Curve und Drawdown aktualisieren (Unverändert) ---
        current_total_equity = equity + unrealized_pnl
        equity_curve.append({'timestamp': ts, 'equity': current_total_equity})

        peak_equity = max(peak_equity, current_total_equity)
        drawdown = (peak_equity - current_total_equity) / peak_equity if peak_equity > 0 else 0
        if drawdown > max_drawdown_pct:
            max_drawdown_pct = drawdown
            max_drawdown_date = ts

        min_equity_ever = min(min_equity_ever, current_total_equity)
        if current_total_equity <= 0 and not liquidation_date:
            liquidation_date = ts

    # --- 4. Ergebnisse vorbereiten (Unverändert) ---
    print("4/4: Bereite Analyse-Ergebnisse vor...")
    final_equity = equity_curve[-1]['equity'] if equity_curve else start_capital
    total_pnl_pct = (final_equity / start_capital - 1) * 100 if start_capital > 0 else 0
    wins = sum(1 for t in trade_history if t['pnl'] > 0)
    win_rate = (wins / len(trade_history) * 100) if trade_history else 0

    trade_df = pd.DataFrame(trade_history)
    pnl_per_strategy = trade_df.groupby('strategy_key')['pnl'].sum().reset_index() if not trade_df.empty else pd.DataFrame(columns=['strategy_key', 'pnl'])
    trades_per_strategy = trade_df.groupby('strategy_key').size().reset_index(name='trades') if not trade_df.empty else pd.DataFrame(columns=['strategy_key', 'trades'])

    equity_df = pd.DataFrame(equity_curve)
    if not equity_df.empty:
        equity_df['peak'] = equity_df['equity'].cummax()
        equity_df['drawdown_pct'] = ((equity_df['peak'] - equity_df['equity']) / equity_df['peak'].replace(0, np.nan)).fillna(0)
        equity_df['timestamp'] = pd.to_datetime(equity_df['timestamp'])
        equity_df.set_index('timestamp', inplace=True, drop=False)

    print("Analyse abgeschlossen.")

    return {
        "start_capital": start_capital,
        "end_capital": final_equity,
        "total_pnl_pct": total_pnl_pct,
        "trade_count": len(trade_history),
        "win_rate": win_rate,
        "max_drawdown_pct": max_drawdown_pct * 100,
        "max_drawdown_date": max_drawdown_date,
        "min_equity": min_equity_ever,
        "liquidation_date": liquidation_date,
        "pnl_per_strategy": pnl_per_strategy,
        "trades_per_strategy": trades_per_strategy,
        "equity_curve": equity_df
    }

# ... (if __name__ == "__main__": bleibt unverändert)
