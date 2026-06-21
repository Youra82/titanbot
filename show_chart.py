#!/usr/bin/env python3
"""
show_chart.py — Simuliert einen SMC-Chart und sendet ihn per Telegram.

Lädt OHLCV-Daten, berechnet SMC-Zonen und schickt einen PNG-Chart
mit simulierten Entry/SL/TP-Levels. Kein echter Trade wird platziert.

Aufruf:
    python show_chart.py
    python show_chart.py --symbol LTC/USDT:USDT --timeframe 6h
    python show_chart.py --symbol BTC/USDT:USDT --timeframe 1h --side buy
"""
import argparse
import json
import logging
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

import ta

from titanbot.strategy.smc_engine import SMCEngine
from titanbot.utils.exchange import Exchange
from titanbot.utils.trade_manager import _generate_smc_chart_png
from titanbot.utils.telegram import send_photo, send_message

logging.basicConfig(level=logging.WARNING, format='[%(levelname)s] %(message)s')
logger = logging.getLogger('show_chart')

CONFIGS_DIR = os.path.join(PROJECT_ROOT, 'src', 'titanbot', 'strategy', 'configs')
TMP_DIR     = os.path.join(PROJECT_ROOT, 'artifacts', 'tmp')


def _load_secrets():
    with open(os.path.join(PROJECT_ROOT, 'secret.json')) as f:
        return json.load(f)


def _load_settings():
    with open(os.path.join(PROJECT_ROOT, 'settings.json')) as f:
        return json.load(f)


def _load_config(symbol: str, timeframe: str) -> dict:
    safe = f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"
    path = os.path.join(CONFIGS_DIR, f'config_{safe}.json')
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def generate_and_send(exchange: Exchange, symbol: str, timeframe: str,
                      signal_side: str, tg: dict) -> bool:
    config     = _load_config(symbol, timeframe)
    smc_params = config.get('strategy', {})

    print(f"  Lade OHLCV {symbol} ({timeframe})...")
    df = exchange.fetch_recent_ohlcv(symbol, timeframe, limit=300)
    if df is None or df.empty or len(df) < 90:
        print(f"  WARNUNG: Nicht genug Daten.")
        return False

    # Indikatoren berechnen (identisch zum Live-Bot)
    atr_ind = ta.volatility.AverageTrueRange(
        high=df['high'], low=df['low'], close=df['close'], window=14)
    df['atr'] = atr_ind.average_true_range()

    adx_ind = ta.trend.ADXIndicator(
        high=df['high'], low=df['low'], close=df['close'], window=14)
    df['adx'] = adx_ind.adx()
    df.dropna(subset=['atr', 'adx'], inplace=True)

    if df.empty:
        print("  WARNUNG: DataFrame nach dropna leer.")
        return False

    # SMC-Analyse
    engine      = SMCEngine(settings=smc_params)
    smc_results = engine.process_dataframe(df[['open', 'high', 'low', 'close']].copy())

    # Simulierte Trade-Levels aus letzter Kerze + ATR
    last        = df.iloc[-1]
    entry_price = float(last['close'])
    atr         = float(last['atr'])
    atr_mult    = config.get('risk', {}).get('atr_multiplier_sl', 2.0)
    rr          = config.get('risk', {}).get('risk_reward_ratio', 2.0)
    sl_dist     = atr * atr_mult

    if signal_side == 'buy':
        sl_price = entry_price - sl_dist
        tp_price = entry_price + sl_dist * rr
    else:
        sl_price = entry_price + sl_dist
        tp_price = entry_price - sl_dist * rr

    print(f"  Entry: {entry_price:.6g} | SL: {sl_price:.6g} | TP: {tp_price:.6g}")

    os.makedirs(TMP_DIR, exist_ok=True)
    path = _generate_smc_chart_png(
        df, smc_results, symbol, timeframe,
        entry_price, sl_price, tp_price, signal_side,
    )

    if not path or not os.path.exists(path):
        print("  FEHLER: PNG konnte nicht erstellt werden.")
        return False

    side_label = 'LONG' if signal_side == 'buy' else 'SHORT'
    caption = (
        f"[SIMULATION] TITANBOT | {symbol} ({timeframe})\n"
        f"{side_label} @ {entry_price:.6g}  |  SL: {sl_price:.6g}  |  TP: {tp_price:.6g}"
    )
    send_photo(tg['bot_token'], tg['chat_id'], path, caption)
    os.remove(path)
    print("  Chart gesendet.")
    return True


def main():
    parser = argparse.ArgumentParser(description='SMC-Chart simulieren und per Telegram senden')
    parser.add_argument('--symbol',    type=str, help='Symbol (z.B. LTC/USDT:USDT)')
    parser.add_argument('--timeframe', type=str, help='Timeframe (z.B. 6h)')
    parser.add_argument('--side',      type=str, default='buy',
                        choices=['buy', 'sell'], help='Simulierte Trade-Richtung (default: buy)')
    args = parser.parse_args()

    secrets  = _load_secrets()
    settings = _load_settings()

    tg = secrets.get('telegram', {})
    if not tg.get('bot_token') or not tg.get('chat_id'):
        print("FEHLER: Kein Telegram-Token/Chat-ID in secret.json.")
        sys.exit(1)

    account = secrets.get('titanbot', [None])[0]
    if not account:
        print("FEHLER: Kein 'titanbot'-Account in secret.json.")
        sys.exit(1)

    print("Initialisiere Exchange...")
    exchange = Exchange(account)
    if not exchange.markets:
        print("FEHLER: Exchange konnte nicht initialisiert werden.")
        sys.exit(1)

    active = settings['live_trading_settings']['active_strategies']

    if args.symbol or args.timeframe:
        targets = [
            s for s in active
            if (not args.symbol    or s['symbol']    == args.symbol)
            and (not args.timeframe or s['timeframe'] == args.timeframe)
        ]
    else:
        targets = [s for s in active if s.get('active', False)]

    if not targets:
        print("Keine passenden Strategien gefunden.")
        sys.exit(1)

    print(f"\n{len(targets)} Strategie(n) — generiere Charts...\n")
    send_message(tg['bot_token'], tg['chat_id'],
                 f"TITANBOT Chart-Simulation ({len(targets)} Strategie(n))")

    ok = 0
    for s in targets:
        symbol    = s['symbol']
        timeframe = s['timeframe']
        side      = args.side
        print(f"[{symbol} / {timeframe} / {side.upper()}]")
        try:
            if generate_and_send(exchange, symbol, timeframe, side, tg):
                ok += 1
        except Exception as e:
            print(f"  FEHLER: {e}")

    print(f"\nFertig: {ok}/{len(targets)} Charts gesendet.")


if __name__ == '__main__':
    main()
