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
from titanbot.strategy.trade_logic import get_titan_signal, get_zone_based_tp
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
                volume_ma_period = smc_params.get('volume_ma_period', 20)

                if len(temp_data) >= 15:
                    # ATR
                    atr_indicator = ta.volatility.AverageTrueRange(high=temp_data['high'], low=temp_data['low'], close=temp_data['close'], window=14)
                    temp_data['atr'] = atr_indicator.average_true_range()

                    # ADX
                    adx_indicator = ta.trend.ADXIndicator(high=temp_data['high'], low=temp_data['low'], close=temp_data['close'], window=adx_period)
                    temp_data['adx'] = adx_indicator.adx()
                    temp_data['adx_pos'] = adx_indicator.adx_pos()
                    temp_data['adx_neg'] = adx_indicator.adx_neg()
                    
                    # Volume MA (NEU)
                    temp_data['volume_ma'] = temp_data['volume'].rolling(window=volume_ma_period).mean()
                    
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
            smc_result = engine.process_dataframe(strat['data'][['open','high','low','close']].copy())
            smc_results_by_strategy[key] = smc_result
            # SMC-Spalten (P/D, Sweep-Flags) in Strategie-Daten übertragen — wie in backtester.py
            enriched_df = smc_result.get('enriched_df')
            if enriched_df is not None:
                for col in enriched_df.columns:
                    if col.startswith('smc_'):
                        strat['data'][col] = enriched_df[col].values
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
                trade_history.append({
                    'strategy_key': key,
                    'symbol':        strat_data['symbol'],
                    'timeframe':     pos.get('timeframe', ''),
                    'direction':     pos['side'],
                    'entry_time':    str(pos.get('entry_ts', ''))[:16].replace('T', ' '),
                    'ts':            pos.get('entry_ts', ts),
                    'entry':         round(pos['entry_price'], 6),
                    'exit':          round(exit_price, 6),
                    'leverage':      pos.get('leverage', 0),
                    'margin_used':   round(pos['margin_used'], 2),
                    'sl_pct':        pos.get('sl_pct', 0),
                    'tsl_activation_rr':  pos.get('tsl_activation_rr', 0),
                    'tsl_callback_pct':   pos.get('tsl_callback_pct', 0),
                    'pnl':           round(pnl_usd - total_fees, 4),
                })
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
                    
                    # FEHLER BEHOBEN: market_bias an die Signalfunktion übergeben, signal_context empfangen
                    side, _, signal_context = get_titan_signal(smc_results, current_candle, params=params_for_logic, market_bias=market_bias) 

                    if side:
                        entry_price = current_candle['close']
                        risk_per_trade_pct = risk_params.get('risk_per_trade_pct', 1.0) / 100
                        risk_reward_ratio = risk_params.get('risk_reward_ratio', 2.0)
                        max_leverage = risk_params.get('max_leverage', 20)
                        min_leverage = risk_params.get('min_leverage', 3)
                        sl_buffer_atr_mult = risk_params.get('sl_buffer_atr_mult', 0.2)
                        activation_rr = risk_params.get('trailing_stop_activation_rr', 2.0)
                        callback_rate = risk_params.get('trailing_stop_callback_rate_pct', 1.0) / 100

                        current_atr = current_candle.get('atr')
                        if pd.isna(current_atr) or current_atr <= 0:
                            continue

                        # --- SMC-Zonenbasierter SL (hinter die Zone) ---
                        buffer = current_atr * sl_buffer_atr_mult
                        zone_low = signal_context.get('level_low', entry_price)
                        zone_high = signal_context.get('level_high', entry_price)
                        if side == 'buy':
                            stop_loss = zone_low - buffer
                        else:
                            stop_loss = zone_high + buffer

                        sl_distance = abs(entry_price - stop_loss)
                        sl_distance = max(sl_distance, entry_price * 0.001)
                        if sl_distance <= 0:
                            continue

                        # --- Variabler Hebel: Risk-basiertes Position Sizing ---
                        risk_amount_usd = equity * risk_per_trade_pct
                        sl_pct = sl_distance / entry_price
                        if sl_pct <= 1e-6:
                            continue

                        target_notional = risk_amount_usd / sl_pct
                        # Hebel klemmen: min_leverage ≤ eff_leverage ≤ max_leverage
                        eff_leverage = target_notional / equity
                        eff_leverage = max(min_leverage, min(eff_leverage, max_leverage))
                        eff_leverage = max(1, math.floor(eff_leverage))  # Bitget: ganzzahliger Hebel, floor = zugunsten SL
                        final_notional_value = min(equity * eff_leverage, absolute_max_notional_value)

                        if final_notional_value < min_notional:
                            # Notional zu klein, Hebel hochsetzen
                            lev_needed = math.ceil(min_notional / equity)
                            if lev_needed <= max_leverage:
                                eff_leverage = lev_needed
                                final_notional_value = equity * eff_leverage
                            else:
                                continue  # Kapital zu gering, selbst bei max_leverage

                        # Echte Margin: notional / eff_leverage (= equity bei vollem Einsatz)
                        margin_used = round(final_notional_value / eff_leverage, 2)

                        current_total_margin = sum(p['margin_used'] for p in open_positions.values())
                        if current_total_margin + margin_used > equity:
                            continue

                        bar_idx = strat['data'].index.get_loc(ts)
                        take_profit = get_zone_based_tp(side, entry_price, sl_distance, risk_reward_ratio, smc_results_by_strategy.get(key, {}), bar_idx)
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
                            'last_known_price': entry_price,
                            # Für Trade-History-Export
                            'entry_ts': ts,
                            'timeframe': strat['timeframe'],
                            'leverage': round(eff_leverage, 1),
                            'sl_pct': round(sl_distance / entry_price * 100, 4),
                            'tsl_activation_rr': activation_rr,
                            'tsl_callback_pct': callback_rate * 100,
                        }

        # --- 3c. Equity Curve und Drawdown aktualisieren ---
        current_total_equity = equity + unrealized_pnl

        # Liquidation: mark-to-market Kapital ≤ 0 → alle Positionen zwangsgeschlossen
        if current_total_equity <= 0 and not liquidation_date:
            liquidation_date = ts
            current_total_equity = 0.0
            open_positions.clear()
            equity = 0.0

        equity_curve.append({'timestamp': ts, 'equity': max(0.0, current_total_equity)})

        peak_equity = max(peak_equity, current_total_equity)
        drawdown = (peak_equity - max(0.0, current_total_equity)) / peak_equity if peak_equity > 0 else 0
        if drawdown > max_drawdown_pct:
            max_drawdown_pct = drawdown
            max_drawdown_date = ts

        min_equity_ever = min(min_equity_ever, current_total_equity)

    # --- 4. Offene Positionen am Backtest-Ende schließen (zum letzten bekannten Kurs) ---
    for key, pos in list(open_positions.items()):
        last_price = pos.get('last_known_price', pos['entry_price'])
        pnl_pct = (last_price / pos['entry_price'] - 1) if pos['side'] == 'long' else (1 - last_price / pos['entry_price'])
        pnl_usd = pos['notional_value'] * pnl_pct
        total_fees = pos['notional_value'] * fee_pct * 2
        net_pnl = pnl_usd - total_fees
        equity += net_pnl
        strat_data = valid_strategies.get(key, {})
        trade_history.append({
            'strategy_key': key,
            'symbol':        strat_data.get('symbol', ''),
            'timeframe':     pos.get('timeframe', ''),
            'direction':     pos['side'],
            'entry':         pos['entry_price'],
            'exit':          last_price,
            'entry_time':    pos.get('entry_ts', ''),
            'exit_time':     'Backtest-Ende',
            'margin_used':   pos.get('margin_used', 0),
            'leverage':      pos.get('leverage', 0),
            'sl_pct':        pos.get('sl_pct', 0),
            'tsl_activation_rr':  pos.get('tsl_activation_rr', 0),
            'tsl_callback_pct':   pos.get('tsl_callback_pct', 0),
            'pnl':           round(net_pnl, 4),
        })
    open_positions.clear()

    # --- 5. Ergebnisse vorbereiten ---
    print("4/4: Bereite Analyse-Ergebnisse vor...")
    final_equity = max(0.0, equity)
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
        "equity_curve":   equity_df,
        "trade_history":  trade_history,
    }

# ... (if __name__ == "__main__": bleibt unverändert)
