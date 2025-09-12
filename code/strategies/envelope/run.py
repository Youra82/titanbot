# code/strategies/envelope/run.py

import os
import sys
import json
import logging
import traceback
import sqlite3
import numpy as np
import pandas as pd
import time

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..', '..', '..')
sys.path.append(os.path.join(PROJECT_ROOT, 'code'))

from utilities.bitget_futures import BitgetFutures
from utilities.strategy_logic import calculate_smc_indicators
from utilities.telegram_handler import send_telegram_message

LOG_DIR = os.path.join(PROJECT_ROOT, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'livetradingbot.log')
logging.basicConfig(level=logging.INFO, format='%(asctime)s UTC %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S',
                    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
logger = logging.getLogger('titan_bot')

def load_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    with open(config_path, 'r') as f:
        return json.load(f)

params = load_config()
SYMBOL = params['market']['symbol']

def get_db_file_path(account_name):
    safe_account_name = "".join(c for c in account_name if c.isalnum() or c in (' ', '_')).rstrip()
    return os.path.join(os.path.dirname(__file__), f"titan_state_{safe_account_name}_{SYMBOL.replace('/', '-')}.db")

def setup_database(account_name):
    db_file = get_db_file_path(account_name)
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value TEXT)''')
    cursor.execute("INSERT OR IGNORE INTO bot_state (key, value) VALUES (?, ?)", ('last_signal_ts', '0'))
    cursor.execute("INSERT OR IGNORE INTO bot_state (key, value) VALUES (?, ?)", ('trailing_stop_active', 'False'))
    conn.commit()
    conn.close()

def get_state(account_name, key):
    db_file = get_db_file_path(account_name)
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM bot_state WHERE key = ?", (key,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else '0'

def set_state(account_name, key, value):
    db_file = get_db_file_path(account_name)
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    cursor.execute("REPLACE INTO bot_state (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

def run_for_account(account, telegram_config):
    account_name = account.get('name', 'Standard-Account')
    bot_token = telegram_config.get('bot_token')
    chat_id = telegram_config.get('chat_id')
    
    logger.info(f"--- Starte TitanBot Ausführung für Account: {account_name} | Symbol: {SYMBOL} ---")
    
    bitget = BitgetFutures(account)
    setup_database(account_name)

    try:
        test_mode = params.get('debug', {}).get('test_mode', False)

        # --- Load recent data and calculate indicators ---
        data = bitget.fetch_recent_ohlcv(SYMBOL, params['market']['timeframe'], 500)
        data = calculate_smc_indicators(data, params['strategy'])
        latest = data.iloc[-1]

        if test_mode:
            logger.warning(f"[{account_name}] TEST-MODUS AKTIV: Sofortiger Trade wird simuliert.")

            forced_value = params.get('debug', {}).get('force_test_order_value_usdt', 10.0)
            if not forced_value or forced_value <= 0:
                logger.error(f"[{account_name}] 'force_test_order_value_usdt' ist ungültig. Test abgebrochen.")
                return

            current_price = bitget.fetch_ticker(SYMBOL).get('last')
            if not current_price or current_price <= 0:
                logger.error(f"[{account_name}] Ungültiger Tickerpreis. Test abgebrochen.")
                return

            # --- Bestimme Side anhand Trend der letzten Kerze ---
            side = 'buy' if latest['trend'] >= 0 else 'sell'
            entry_price = current_price
            sl_distance = latest['atr'] if not np.isnan(latest['atr']) else current_price * 0.01
            stop_loss_price = entry_price - sl_distance if side == 'buy' else entry_price + sl_distance
            rr = params['risk']['risk_reward_ratio']
            take_profit_price = entry_price + sl_distance * rr if side == 'buy' else entry_price - sl_distance * rr

            # Menge berechnen anhand forced_value
            amount = forced_value / entry_price

            leverage = params['risk']['leverage']
            margin_mode = params['risk']['margin_mode']

            bitget.set_margin_mode(SYMBOL, margin_mode)
            bitget.set_leverage(SYMBOL, leverage, margin_mode)

            logger.info(f"[{account_name}] Platzierung Test-Orders: {side.upper()} Entry={entry_price:.4f}, SL={stop_loss_price:.4f}, TP={take_profit_price:.4f}, Menge={amount:.4f}")
            
            order = bitget.place_limit_order(
                SYMBOL,
                side,
                amount,
                entry_price,
                params={'leverage': leverage, 'marginMode': margin_mode, 'postOnly': True}
            )

            close_side = 'sell' if side == 'buy' else 'buy'
            bitget.place_trigger_market_order(SYMBOL, close_side, amount, take_profit_price, {'reduce': True})
            bitget.place_trigger_market_order(SYMBOL, close_side, amount, stop_loss_price, {'reduce': True})

            message = f"📊 TEST-SIGNAL für Account *{account_name}* ({SYMBOL}, {side.upper()})\n" \
                      f"- Entry: ${entry_price:.4f}\n- SL: ${stop_loss_price:.4f}\n- TP: ${take_profit_price:.4f}\n- Menge: {amount:.4f}"
            send_telegram_message(bot_token, chat_id, message)

            logger.warning(f"[{account_name}] TEST-TRADE abgeschlossen.")
            return

        # --- Live-Modus Logik wie bisher ---
        position = bitget.fetch_open_positions(SYMBOL)
        if position:
            pos = position[0]
            entry_price = float(pos['entryPrice'])
            side = pos['side']
            trailing_stop_active = get_state(account_name, 'trailing_stop_active') == 'True'
            if not trailing_stop_active:
                activation_rr = params['risk']['trailing_stop_activation_rr']
                last_signal_ts_str = get_state(account_name, 'last_signal_ts')
                if last_signal_ts_str == '0': return
                signal_timestamp = pd.to_datetime(int(last_signal_ts_str), unit='s', utc=True)
                if signal_timestamp not in data.index: return
                signal_data = data.loc[signal_timestamp]
                initial_sl = signal_data['ob_low'] if side == 'long' else signal_data['ob_high']
                if np.isnan(initial_sl): return

                risk_distance = abs(entry_price - initial_sl)
                activation_price = entry_price + (risk_distance * activation_rr) if side == 'long' else entry_price - (risk_distance * activation_rr)
                current_price = bitget.fetch_ticker(SYMBOL)['last']
                if (side == 'long' and current_price >= activation_price) or (side == 'short' and current_price <= activation_price):
                    logger.info(f"[{account_name}] Aktivierungspreis ${activation_price:.4f} erreicht. Aktiviere Trailing Stop Loss.")
                    bitget.cancel_all_trigger_orders(SYMBOL)
                    callback_rate = params['risk']['trailing_stop_callback_rate_pct']
                    contracts = float(pos['contracts'])
                    close_side = 'sell' if side == 'long' else 'buy'
                    bitget.place_trailing_stop_order(SYMBOL, close_side, contracts, callback_rate, current_price)
                    set_state(account_name, 'trailing_stop_active', 'True')
                    message = f"🚀 Trailing Stop für *{account_name}* ({SYMBOL}) aktiviert!\n- Gewinn ist bei >{activation_rr}:1 gesichert."
                    send_telegram_message(bot_token, chat_id, message)
            else:
                logger.info(f"[{account_name}] Trailing Stop ist bereits aktiv. Management durch Bitget.")
            return

        # Keine Position, prüfe neue Signale
        if get_state(account_name, 'trailing_stop_active') == 'True':
            set_state(account_name, 'trailing_stop_active', 'False')

        if bitget.fetch_open_orders(SYMBOL):
            logger.info(f"[{account_name}] Warte auf Ausführung der Limit-Order für {SYMBOL}.")
            return

        logger.info(f"[{account_name}] Keine Position offen. Suche nach neuen SMC-Einstiegen.")
        bitget.cancel_all_orders(SYMBOL)
        bitget.cancel_all_trigger_orders(SYMBOL)

        latest_signal = data.iloc[-2]
        if not np.isnan(latest_signal['bos_level']) and not np.isnan(latest_signal['ob_high']):
            signal_ts = int(latest_signal.name.timestamp())
            last_signal_ts = int(get_state(account_name, 'last_signal_ts'))

            if signal_ts > last_signal_ts:
                entry_price, stop_loss_price, side = None, None, None
                if latest_signal['trend'] == 1 and params['behavior']['use_longs'] and latest_signal['low'] > latest_signal['ob_high']:
                    entry_price, stop_loss_price, side = latest_signal['ob_high'], latest_signal['ob_low'], 'buy'
                elif latest_signal['trend'] == -1 and params['behavior']['use_shorts']
