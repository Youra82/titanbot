# src/titanbot/analysis/portfolio_simulator.py (Version für TitanBot SMC)
import pandas as pd
import numpy as np
from tqdm import tqdm
import sys
import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

# *** Geänderte Imports: SMC Engine und Trade Logic ***
from titanbot.strategy.smc_engine import SMCEngine, Bias
from titanbot.strategy.trade_logic import get_titan_signal # Nutzt die Live-Logik

def run_portfolio_simulation(start_capital, strategies_data, start_date, end_date):
    """
    Führt eine chronologische Portfolio-Simulation mit mehreren SMC-Strategien durch.
    'strategies_data' erwartet jetzt Keys (z.B. 'BTC_1h') und Dictionaries mit
    'symbol', 'timeframe', 'data', 'smc_params', 'risk_params'.
    """
    print("\n--- Starte Portfolio-Simulation (SMC)... ---")

    # --- 1. Kombiniere alle Zeitstempel ---
    all_timestamps = set()
    print("1/4: Sammle Zeitstempel aller Strategien...")
    for key, strat in strategies_data.items():
        if 'data' in strat and not strat['data'].empty:
            all_timestamps.update(strat['data'].index)
        else:
            print(f"WARNUNG: Keine Daten für Strategie {key} gefunden.")

    if not all_timestamps:
        print("Keine gültigen Daten für die Simulation gefunden.")
        return None

    sorted_timestamps = sorted(list(all_timestamps))
    print(f"-> {len(sorted_timestamps)} eindeutige Zeitstempel gefunden.")

    # --- 2. SMC-Analyse für jede Strategie (einmalig) ---
    print("2/4: Führe SMC-Analyse für alle Strategien durch...")
    smc_results_by_strategy = {}
    valid_strategies = {} # Nur Strategien mit erfolgreicher Analyse
    for key, strat in tqdm(strategies_data.items(), desc="SMC Analyse"):
         if 'data' in strat and not strat['data'].empty:
            try:
                engine = SMCEngine(settings=strat.get('smc_params', {}))
                smc_results_by_strategy[key] = engine.process_dataframe(strat['data'].copy())
                valid_strategies[key] = strat # Füge zu gültigen Strategien hinzu
            except Exception as e:
                print(f"FEHLER bei SMC-Analyse für {key}: {e}")
         else:
             print(f"Überspringe SMC-Analyse für {key} wegen fehlender Daten.")

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

    open_positions = {} # Key: strategy_key (z.B. "BTC_1h"), Value: Positionsdetails
    trade_history = []
    equity_curve = []

    for ts in tqdm(sorted_timestamps, desc="Simuliere Portfolio"):
        if liquidation_date: break # Simulation stoppen bei Liquidation

        current_total_equity = equity # Startkapital der Kerze
        unrealized_pnl = 0

        # --- 3a. Offene Positionen managen ---
        positions_to_close = []
        for key, pos in open_positions.items():
            strat_data = valid_strategies.get(key)
            if not strat_data or ts not in strat_data['data'].index:
                # Daten für diese Kerze nicht verfügbar (passiert bei unterschiedlichen Timeframes)
                # Aktualisiere PnL mit letztem bekannten Preis, falls möglich
                if pos.get('last_known_price'):
                     pnl_mult = 1 if pos['side'] == 'long' else -1
                     unrealized_pnl += pos['notional_value'] * (pos['last_known_price'] / pos['entry_price'] -1) * pnl_mult
                continue

            current_candle = strat_data['data'].loc[ts]
            pos['last_known_price'] = current_candle['close'] # Für nächste Iteration speichern
            exit_price = None

            # Trailing Stop & Exit Logik (wie im Einzel-Backtester)
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

            # Position schließen
            if exit_price:
                pnl_pct = (exit_price / pos['entry_price'] - 1) if pos['side'] == 'long' else (1 - exit_price / pos['entry_price'])
                pnl_usd = pos['notional_value'] * pnl_pct
                total_fees = pos['notional_value'] * (0.05 / 100) * 2 # Standard-Fee
                equity += (pnl_usd - total_fees) # Realisiere Gewinn/Verlust
                trade_history.append({'strategy_key': key, 'symbol': strat_data['symbol'], 'pnl': (pnl_usd - total_fees)})
                positions_to_close.append(key)
            else:
                 # Unrealisierten PnL für Equity Curve berechnen
                 pnl_mult = 1 if pos['side'] == 'long' else -1
                 unrealized_pnl += pos['notional_value'] * (current_candle['close'] / pos['entry_price'] -1) * pnl_mult

        # Geschlossene Positionen entfernen
        for key in positions_to_close:
            del open_positions[key]

        # --- 3b. Neue Signale prüfen und Positionen eröffnen ---
        if equity > 0: # Nur handeln, wenn nicht liquidiert
            for key, strat in valid_strategies.items():
                # Nur prüfen, wenn keine Position für DIESE Strategie offen ist
                # UND Daten für diesen Zeitstempel vorhanden sind
                if key not in open_positions and ts in strat['data'].index:
                    current_candle = strat['data'].loc[ts]
                    smc_results = smc_results_by_strategy.get(key)
                    risk_params = strat.get('risk_params', {})
                    
                    if not smc_results: continue # SMC Analyse fehlgeschlagen

                    # Nutze die zentrale Trade-Logik
                    side, _ = get_titan_signal(smc_results, current_candle, params={})

                    if side:
                        entry_price = current_candle['close']
                        risk_per_trade_pct = risk_params.get('risk_per_trade_pct', 1.0) / 100
                        risk_reward_ratio = risk_params.get('risk_reward_ratio', 2.0)
                        leverage = risk_params.get('leverage', 10)
                        activation_rr = risk_params.get('trailing_stop_activation_rr', 2.0)
                        callback_rate = risk_params.get('trailing_stop_callback_rate_pct', 1.0) / 100

                        # Feste SL-Distanz (BESSER: SMC-basiert)
                        sl_distance_pct = 0.015
                        sl_distance = entry_price * sl_distance_pct
                        if sl_distance == 0: continue # Ungültiger SL

                        risk_amount_usd = equity * risk_per_trade_pct # Risiko vom Gesamt-Equity
                        notional_value = risk_amount_usd / sl_distance_pct
                        margin_used = notional_value / leverage

                        # Einfache Prüfung: Gesamte verwendete Margin darf Equity nicht übersteigen
                        current_total_margin = sum(p['margin_used'] for p in open_positions.values())
                        if current_total_margin + margin_used > equity:
                             # print(f"WARNUNG: Nicht genug Equity für Trade {key} bei {ts}")
                             continue # Nicht genug freies Kapital

                        stop_loss = entry_price - sl_distance if side == 'buy' else entry_price + sl_distance
                        take_profit = entry_price + sl_distance * risk_reward_ratio if side == 'buy' else entry_price - sl_distance * risk_reward_ratio
                        activation_price = entry_price + sl_distance * activation_rr if side == 'buy' else entry_price - sl_distance * activation_rr

                        open_positions[key] = {
                            'side': 'long' if side == 'buy' else 'short',
                            'entry_price': entry_price,
                            'stop_loss': stop_loss,
                            'take_profit': take_profit,
                            'notional_value': notional_value,
                            'margin_used': margin_used,
                            'trailing_active': False,
                            'activation_price': activation_price,
                            'peak_price': entry_price,
                            'callback_rate': callback_rate,
                            'last_known_price': entry_price # Initialisieren
                        }

        # --- 3c. Equity Curve und Drawdown aktualisieren ---
        current_total_equity = equity + unrealized_pnl # Realisiertes Kapital + Unrealisierter PnL
        equity_curve.append({'timestamp': ts, 'equity': current_total_equity})

        peak_equity = max(peak_equity, current_total_equity)
        drawdown = (peak_equity - current_total_equity) / peak_equity if peak_equity > 0 else 0
        if drawdown > max_drawdown_pct:
            max_drawdown_pct = drawdown
            max_drawdown_date = ts

        min_equity_ever = min(min_equity_ever, current_total_equity)
        if current_total_equity <= 0 and not liquidation_date:
            liquidation_date = ts

    # --- 4. Ergebnisse vorbereiten ---
    print("4/4: Bereite Analyse-Ergebnisse vor...")
    final_equity = equity_curve[-1]['equity'] if equity_curve else start_capital
    total_pnl_pct = (final_equity / start_capital - 1) * 100 if start_capital > 0 else 0
    wins = sum(1 for t in trade_history if t['pnl'] > 0)
    win_rate = (wins / len(trade_history) * 100) if trade_history else 0

    # PnL und Trade-Anzahl pro Strategie
    trade_df = pd.DataFrame(trade_history)
    pnl_per_strategy = trade_df.groupby('strategy_key')['pnl'].sum().reset_index() if not trade_df.empty else pd.DataFrame(columns=['strategy_key', 'pnl'])
    trades_per_strategy = trade_df.groupby('strategy_key').size().reset_index(name='trades') if not trade_df.empty else pd.DataFrame(columns=['strategy_key', 'trades'])

    equity_df = pd.DataFrame(equity_curve)
    if not equity_df.empty:
        equity_df['peak'] = equity_df['equity'].cummax()
        equity_df['drawdown_pct'] = ((equity_df['peak'] - equity_df['equity']) / equity_df['peak'].replace(0, np.nan)).fillna(0)
        # Stelle sicher, dass der Timestamp-Index korrekt ist für den Export
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
        "equity_curve": equity_df # <-- WICHTIG: Wir geben das DataFrame zurück
    }

# Optional: Ein kleiner Test, wenn die Datei direkt ausgeführt wird
if __name__ == "__main__":
    from titanbot.analysis.backtester import load_data
    # Beispiel-Setup für einen lokalen Test
    start_cap = 1000
    start_dt = "2024-01-01"
    end_dt = "2024-04-01"
    
    test_strategies = {
        "BTC_1h": {
            'symbol': "BTC/USDT:USDT", 'timeframe': "1h",
            'smc_params': {'swingsLength': 30, 'ob_mitigation': 'High/Low'},
            'risk_params': {'risk_per_trade_pct': 1.0, 'risk_reward_ratio': 2.0, 'leverage': 10}
        },
        "ETH_1h": {
            'symbol': "ETH/USDT:USDT", 'timeframe': "1h",
            'smc_params': {'swingsLength': 50, 'ob_mitigation': 'Close'},
            'risk_params': {'risk_per_trade_pct': 1.5, 'risk_reward_ratio': 1.5, 'leverage': 15}
        }
    }
    
    print("Lade Testdaten...")
    for key in test_strategies:
        strat = test_strategies[key]
        strat['data'] = load_data(strat['symbol'], strat['timeframe'], start_dt, end_dt)
        print(f"Daten für {key} geladen: {len(strat['data'])} Kerzen")

    print("\nStarte Portfolio-Simulationstest...")
    results = run_portfolio_simulation(start_cap, test_strategies, start_dt, end_dt)

    if results:
         print("\n--- TEST ERGEBNISSE ---")
         print(f"Endkapital: {results['end_capital']:.2f}")
         print(f"PnL %: {results['total_pnl_pct']:.2f}%")
         print(f"Max DD %: {results['max_drawdown_pct']:.2f}%")
         print(f"Trades: {results['trade_count']}")
         print("\nEquity Curve Head:")
         print(results['equity_curve'].head())
