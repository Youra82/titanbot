# src/jaegerbot/analysis/portfolio_simulator.py (Version 2 - Korrigiert)
import pandas as pd
import numpy as np
from tqdm import tqdm

from jaegerbot.utils.ann_model import prepare_data_for_ann

def run_portfolio_simulation(start_capital, strategies_data, start_date, end_date):
    """
    Führt eine chronologische Portfolio-Simulation mit mehreren Strategien durch.
    """
    print("\n--- Starte Portfolio-Simulation... ---")

    all_signals = []

    print("1/4: Generiere Handelssignale für alle Strategien...")
    for key, strat in strategies_data.items():
        data = strat['data']
        model = strat['model']
        scaler = strat['scaler']
        params = strat['params']
        timeframe = strat['timeframe']

        X, _ = prepare_data_for_ann(data.copy(), timeframe, verbose=False)
        if X.empty: continue

        data_with_features = data.loc[X.index].copy()
        features_scaled = scaler.transform(X)
        predictions = model.predict(features_scaled, verbose=0).flatten()

        pred_threshold = params.get('prediction_threshold', 0.65)

        long_signals = data_with_features[predictions >= pred_threshold]
        short_signals = data_with_features[predictions <= (1 - pred_threshold)]

        for index, row in long_signals.iterrows():
            all_signals.append({'timestamp': index, 'symbol': strat['symbol'], 'side': 'long', 'entry_price': row['close'], 'params': params})
        for index, row in short_signals.iterrows():
            all_signals.append({'timestamp': index, 'symbol': strat['symbol'], 'side': 'short', 'entry_price': row['close'], 'params': params})

    if not all_signals:
        print("Keine Handelssignale im gewählten Zeitraum gefunden.")
        return None

    all_signals.sort(key=lambda x: x['timestamp'])

    print("2/4: Führe chronologische Backtests durch...")
    equity = start_capital
    peak_equity = start_capital
    max_drawdown_pct = 0.0
    max_drawdown_date = None
    min_equity = start_capital
    liquidation_date = None

    open_positions = {}
    trade_history = []
    equity_curve = []

    all_timestamps = set()
    for key, strat in strategies_data.items():
        all_timestamps.update(strat['data'].index)

    sorted_timestamps = sorted(list(all_timestamps))

    signal_idx = 0
    for ts in tqdm(sorted_timestamps, desc="Simuliere Portfolio"):
        if liquidation_date: break

        positions_to_close = []
        for symbol, pos in open_positions.items():
            current_data = strategies_data[symbol]['data']
            if ts not in current_data.index: continue

            current_candle = current_data.loc[ts]
            exit_price = None

            pnl_multiplier = 1 if pos['side'] == 'long' else -1

            if pos['side'] == 'long':
                if current_candle['low'] <= pos['sl']: exit_price = pos['sl']
                elif current_candle['high'] >= pos['tp']: exit_price = pos['tp']
            else: # short
                if current_candle['high'] >= pos['sl']: exit_price = pos['sl']
                elif current_candle['low'] <= pos['tp']: exit_price = pos['tp']

            if exit_price:
                pnl_pct = (exit_price / pos['entry_price'] - 1) * pnl_multiplier
                pnl_usd = pos['notional_value'] * pnl_pct
                equity += pnl_usd
                trade_history.append({'symbol': symbol, 'pnl': pnl_usd})
                positions_to_close.append(symbol)

        for symbol in positions_to_close:
            del open_positions[symbol]

        while signal_idx < len(all_signals) and all_signals[signal_idx]['timestamp'] == ts:
            signal = all_signals[signal_idx]
            symbol = signal['symbol']

            if symbol not in open_positions:
                params = signal['params']
                risk_per_trade_pct = params.get('risk_per_trade_pct', 1.0) / 100
                risk_reward_ratio = params.get('risk_reward_ratio', 2.0)
                leverage = params.get('leverage', 10)

                risk_amount_usd = equity * risk_per_trade_pct
                if risk_amount_usd <= 0: signal_idx += 1; continue

                entry_price = signal['entry_price']

                sl_distance_pct = 0.015
                notional_value = risk_amount_usd / sl_distance_pct
                amount = notional_value / entry_price
                margin_used = notional_value / leverage

                if margin_used > equity:
                    signal_idx += 1
                    continue

                sl_distance = entry_price * sl_distance_pct
                if signal['side'] == 'long':
                    sl, tp = entry_price - sl_distance, entry_price + sl_distance * risk_reward_ratio
                else:
                    sl, tp = entry_price + sl_distance, entry_price - sl_distance * risk_reward_ratio

                open_positions[symbol] = {'side': signal['side'], 'entry_price': entry_price, 'amount': amount, 'sl': sl, 'tp': tp, 'notional_value': notional_value}

            signal_idx += 1

        current_pnl = 0
        for symbol, pos in open_positions.items():
            current_data = strategies_data[symbol]['data']
            if ts not in current_data.index: continue
            current_price = current_data.loc[ts]['close']
            pnl_multiplier = 1 if pos['side'] == 'long' else -1
            pnl_pct = (current_price / pos['entry_price'] - 1) * pnl_multiplier
            current_pnl += pos['notional_value'] * pnl_pct

        current_equity = equity + current_pnl
        equity_curve.append({'timestamp': ts, 'equity': current_equity})

        peak_equity = max(peak_equity, current_equity)
        drawdown = (peak_equity - current_equity) / peak_equity if peak_equity > 0 else 0
        if drawdown > max_drawdown_pct:
            max_drawdown_pct = drawdown
            max_drawdown_date = ts

        min_equity = min(min_equity, current_equity)
        if current_equity <= 0 and not liquidation_date:
            liquidation_date = ts

    print("3/4: Bereite Analyse-Ergebnisse vor...")
    final_equity = equity_curve[-1]['equity'] if equity_curve else start_capital
    total_pnl_pct = (final_equity / start_capital - 1) * 100 if start_capital > 0 else 0
    wins = sum(1 for t in trade_history if t['pnl'] > 0)
    win_rate = (wins / len(trade_history) * 100) if trade_history else 0

    pnl_per_strategy = pd.DataFrame(trade_history).groupby('symbol')['pnl'].sum().reset_index()
    trades_per_strategy = pd.DataFrame(trade_history).groupby('symbol').size().reset_index(name='trades')

    equity_df = pd.DataFrame(equity_curve)
    if not equity_df.empty:
        equity_df['peak'] = equity_df['equity'].cummax()
        equity_df['drawdown_pct'] = ((equity_df['peak'] - equity_df['equity']) / equity_df['peak']).fillna(0)

    print("4/4: Analyse abgeschlossen.")

    return {
        "start_capital": start_capital, "end_capital": final_equity, "total_pnl_pct": total_pnl_pct,
        "trade_count": len(trade_history), "win_rate": win_rate, "max_drawdown_pct": max_drawdown_pct * 100,
        "max_drawdown_date": max_drawdown_date, "min_equity": min_equity, "liquidation_date": liquidation_date,
        "pnl_per_strategy": pnl_per_strategy, "trades_per_strategy": trades_per_strategy,
        "equity_curve": equity_df # <-- WICHTIG: Wir geben das DataFrame zurück
    }
