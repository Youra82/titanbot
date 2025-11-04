# src/titanbot/utils/trade_manager.py
# Vollständig korrigiert – Risikoberechnung, price_to_precision, TSL, 30 USDT
import json
import logging
import os
import time
from datetime import datetime, timedelta

import ccxt
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from titanbot.strategy.trade_logic import get_titan_signal
from titanbot.utils.exchange import Exchange
from titanbot.utils.telegram import send_message

# --------------------------------------------------------------------------- #
# Pfade
# --------------------------------------------------------------------------- #
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
ARTIFACTS_PATH = os.path.join(PROJECT_ROOT, 'artifacts')
DB_PATH = os.path.join(ARTIFACTS_PATH, 'db')
TRADE_LOCK_FILE = os.path.join(DB_PATH, 'trade_lock.json')


# --------------------------------------------------------------------------- #
# Trade-Lock-Hilfsfunktionen
# --------------------------------------------------------------------------- #
def load_or_create_trade_lock():
    os.makedirs(DB_PATH, exist_ok=True)
    if os.path.exists(TRADE_LOCK_FILE):
        with open(TRADE_LOCK_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_trade_lock(trade_lock):
    with open(TRADE_LOCK_FILE, 'w') as f:
        json.dump(trade_lock, f, indent=4)

def is_trade_locked(symbol_timeframe):
    trade_lock = load_or_create_trade_lock()
    lock_time_str = trade_lock.get(symbol_timeframe)
    if lock_time_str:
        lock_time = datetime.strptime(lock_time_str, "%Y-%m-%d %H:%M:%S")
        if datetime.now() < lock_time:
            return True
    return False

def set_trade_lock(symbol_timeframe, lock_duration_minutes=60):
    lock_time = datetime.now() + timedelta(minutes=lock_duration_minutes)
    trade_lock = load_or_create_trade_lock()
    trade_lock[symbol_timeframe] = lock_time.strftime("%Y-%m-%d %H:%M:%S")
    save_trade_lock(trade_lock)


# --------------------------------------------------------------------------- #
# Housekeeper – säubert verwaiste Orders/Positionen
# --------------------------------------------------------------------------- #
def housekeeper_routine(exchange, symbol, logger):
    try:
        logger.info(f"Housekeeper: Starte Aufräumroutine für {symbol}...")
        exchange.cancel_all_orders_for_symbol(symbol)
        time.sleep(2)

        position = exchange.fetch_open_positions(symbol)
        if position:
            pos_info = position[0]
            close_side = 'sell' if pos_info['side'] == 'long' else 'buy'
            logger.warning(f"Housekeeper: Schließe verwaiste Position ({pos_info['side']} {pos_info['contracts']})...")
            exchange.create_market_order(symbol, close_side, float(pos_info['contracts']), {'reduceOnly': True})
            time.sleep(3)

        if exchange.fetch_open_positions(symbol):
            logger.error("Housekeeper: Position konnte nicht geschlossen werden!")
        else:
            logger.info(f"Housekeeper: {symbol} ist jetzt sauber.")
        return True
    except Exception as e:
        logger.error(f"Housekeeper-Fehler: {e}", exc_info=True)
        return False


# --------------------------------------------------------------------------- #
# Hauptfunktion: Trade öffnen + SL/TP/TSL setzen
# --------------------------------------------------------------------------- #
def check_and_open_new_position(exchange, model, scaler, params, telegram_config, logger):
    symbol = params['market']['symbol']
    timeframe = params['market']['timeframe']
    symbol_timeframe = f"{symbol.replace('/', '-')}_{timeframe}"

    if is_trade_locked(symbol_timeframe):
        logger.info(f"Trade für {symbol_timeframe} gesperrt – überspringe.")
        return

    try:
        # --------------------------------------------------- #
        # 1. Daten holen + Signal prüfen
        # --------------------------------------------------- #
        logger.info(f"Prüfe Signal für {symbol} ({timeframe})...")
        recent_data = exchange.fetch_recent_ohlcv(symbol, timeframe, limit=100)
        if recent_data.empty:
            logger.warning("Keine OHLCV-Daten – überspringe.")
            return

        current_candle = recent_data.iloc[-1]
        signal_side, signal_price = get_titan_signal({}, current_candle, params)
        if not signal_side:
            logger.info("Kein Signal – überspringe.")
            return

        if exchange.fetch_open_positions(symbol):
            logger.info("Position bereits offen – überspringe.")
            return

        # --------------------------------------------------- #
        # 2. Margin & Leverage setzen
        # --------------------------------------------------- #
        risk_params = params.get('risk', {})
        leverage = risk_params.get('leverage', 10)
        if not exchange.set_margin_mode(symbol, risk_params.get('margin_mode', 'isolated')):
            logger.error("Margin-Modus konnte nicht gesetzt werden.")
            return
        if not exchange.set_leverage(symbol, leverage):
            logger.error("Leverage konnte nicht gesetzt werden.")
            return

        # --------------------------------------------------- #
        # 3. Balance & Risiko berechnen
        # --------------------------------------------------- #
        balance = exchange.fetch_balance_usdt()
        if balance <= 0:
            logger.error("Kein USDT-Guthaben.")
            return

        risk_pct = risk_params.get('risk_per_trade_pct', 1.0) / 100.0
        risk_usdt = balance * risk_pct

        ticker = exchange.fetch_ticker(symbol)
        entry_price = signal_price or ticker['last']
        if not entry_price:
            logger.error("Kein Entry-Preis.")
            return

        rr = risk_params.get('risk_reward_ratio', 2.0)
        sl_distance_pct = 1.0 / leverage

        if signal_side == 'buy':
            sl_price = entry_price * (1 - sl_distance_pct)
            tp_price = entry_price * (1 + rr * sl_distance_pct)
            pos_side = 'buy'
            tsl_side = 'sell'
        else:
            sl_price = entry_price * (1 + sl_distance_pct)
            tp_price = entry_price * (1 - rr * sl_distance_pct)
            pos_side = 'sell'
            tsl_side = 'buy'

        risk_per_contract = abs(entry_price - sl_price)
        contract_size = exchange.markets[symbol].get('contractSize', 1.0)
        amount = risk_usdt / risk_per_contract / contract_size

        min_amount = exchange.markets[symbol].get('limits', {}).get('amount', {}).get('min', 0.0)
        if amount < min_amount:
            logger.error(f"Ordergröße {amount} < Mindestbetrag {min_amount}.")
            return

        # --------------------------------------------------- #
        # 4. Market-Order eröffnen
        # --------------------------------------------------- #
        logger.info(f"Eröffne {pos_side.upper()}-Position: {amount:.6f} Contracts @ ${entry_price:.6f}")
        entry_order = exchange.create_market_order(symbol, pos_side, amount, {'leverage': leverage})
        if not entry_order:
            logger.error("Market-Order fehlgeschlagen.")
            return

        time.sleep(2)
        position = exchange.fetch_open_positions(symbol)
        if not position:
            logger.error("Position wurde nicht eröffnet.")
            return

        pos_info = position[0]
        entry_price = float(pos_info.get('entryPrice', entry_price))
        contracts = float(pos_info['contracts'])

        # --------------------------------------------------- #
        # 5. SL & TP (Trigger-Market-Orders)
        # --------------------------------------------------- #
        sl_rounded = float(exchange.exchange.price_to_precision(symbol, sl_price))
        tp_rounded = float(exchange.exchange.price_to_precision(symbol, tp_price))

        exchange.place_trigger_market_order(symbol, tsl_side, contracts, sl_rounded, {'reduceOnly': True})

        # --------------------------------------------------- #
        # 6. Trailing-Stop-Loss
        # --------------------------------------------------- #
        act_rr = risk_params.get('trailing_stop_activation_rr', 1.5)
        callback_pct = risk_params.get('trailing_stop_callback_rate_pct', 0.5) / 100.0

        if pos_side == 'buy':
            act_price = entry_price * (1 + act_rr / leverage)
        else:
            act_price = entry_price * (1 - act_rr / leverage)

        act_price_rounded = float(exchange.exchange.price_to_precision(symbol, act_price))

        tsl = exchange.place_trailing_stop_order(
            symbol, tsl_side, contracts, act_price, callback_pct, {'reduceOnly': True}
        )
        if tsl:
            logger.info("Trailing-Stop platziert.")
        else:
            logger.warning("Trailing-Stop fehlgeschlagen – Fallback auf SL.")

        # --------------------------------------------------- #
        # 7. Telegram-Benachrichtigung
        # --------------------------------------------------- #
        if telegram_config and telegram_config.get('bot_token') and telegram_config.get('chat_id'):
            sl_r = float(exchange.exchange.price_to_precision(symbol, sl_price))
            tp_r = float(exchange.exchange.price_to_precision(symbol, tp_price))
            msg = (
                f"NEUER TRADE: {symbol} ({timeframe})\n"
                f"- Richtung: {pos_side.upper()}\n"
                f"- Entry: ${entry_price:.6f}\n"
                f"- SL: ${sl_r:.6f}\n"
                f"- TP: ${tp_r:.6f} (RR: {rr:.2f})\n"
                f"- TSL: Aktivierung @ ${act_price_rounded:.6f}, Callback: {callback_pct*100:.2f}%"
            )
            send_message(telegram_config['bot_token'], telegram_config['chat_id'], msg)

        logger.info("Trade-Eröffnung erfolgreich abgeschlossen.")

    except ccxt.InsufficientFunds as e:
        logger.error(f"InsufficientFunds: {e}")
    except ccxt.ExchangeError as e:
        logger.error(f"Börsenfehler: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Unerwarteter Fehler: {e}", exc_info=True)
        housekeeper_routine(exchange, symbol, logger)


# --------------------------------------------------------------------------- #
# Vollständiger Handelszyklus (wird vom Bot aufgerufen)
# --------------------------------------------------------------------------- #
def full_trade_cycle(exchange, model, scaler, params, telegram_config, logger):
    symbol = params['market']['symbol']
    try:
        pos = exchange.fetch_open_positions(symbol)
        if pos:
            logger.info(f"Position offen – Management via SL/TP/TSL.")
        else:
            housekeeper_routine(exchange, symbol, logger)
            check_and_open_new_position(exchange, model, scaler, params, telegram_config, logger)
    except ccxt.DDoSProtection:
        logger.warning("Rate-Limit – warte 10s.")
        time.sleep(10)
    except ccxt.RequestTimeout:
        logger.warning("Timeout – warte 5s.")
        time.sleep(5)
    except ccxt.NetworkError:
        logger.warning("Netzwerkfehler – warte 10s.")
        time.sleep(10)
    except ccxt.AuthenticationError as e:
        logger.critical(f"Authentifizierungsfehler: {e}")
    except Exception as e:
        logger.error(f"Fehler im Zyklus: {e}", exc_info=True)
        time.sleep(5)
