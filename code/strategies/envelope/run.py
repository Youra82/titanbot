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
logging.basicConfig(level=logging.INFO, format='%(asctime)s UTC %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S', handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
logger = logging.getLogger('titan_bot')

def load_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    with open(config_path, 'r') as f: return json.load(f)

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
        if params.get('debug', {}).get('test_mode', False):
            logger.warning(f"[{account_name}] ACHTUNG: TEST-MODUS IST AKTIV!")
            
            if bitget.fetch_open_positions(SYMBOL):
                logger.warning(f"[{account_name}] TEST-MODUS: Es ist bereits eine Position offen. Bitte manuell schließen.")
                return

            margin_mode = params['risk']['margin_mode']
            leverage = params['risk']['leverage']
            
            logger.info(f"[{account_name}] Setze Margin-Modus auf '{margin_mode}'...")
            bitget.set_margin_mode(SYMBOL, margin_mode)

            logger.info(f"[{account_name}] Setze Hebel auf {leverage}x...")
            bitget.set_leverage(SYMBOL, leverage, margin_mode)

            market_info = bitget.get_market_info(SYMBOL)
            min_cost = market_info.get('limits', {}).get('cost', {}).get('min', 5.0)
            target_cost = min_cost * 1.1 
            
            ticker = bitget.fetch_ticker(SYMBOL)
            current_price = ticker.get('last')
            if not current_price or current_price <= 0:
                logger.error(f"Ungültiger Preis für Benchmark erhalten: {current_price}")
                return

            amount = target_cost / current_price
            
            logger.info(f"[{account_name}] Platziere Test-Market-BUY-Order (Menge: {amount:.4f}, Wert: ~{target_cost:.2f} USDT)...")
            buy_order = bitget.create_market_order(SYMBOL, 'buy', amount)
            logger.info(f"[{account_name}] Test-BUY-Order {buy_order['id']} erfolgreich platziert.")
            
            time.sleep(3) 

            open_pos = bitget.fetch_open_positions(SYMBOL)
            if open_pos:
                contracts_to_close = float(open_pos[0]['contracts'])
                logger.info(f"[{account_name}] Schließe Test-Position (Menge: {contracts_to_close:.4f})...")
                sell_order = bitget.create_market_order(SYMBOL, 'sell', contracts_to_close, params={'reduceOnly': True})
                logger.info(f"[{account_name}] Test-Position mit Order {sell_order['id']} erfolgreich geschlossen.")
            else:
                 logger.warning(f"[{account_name}] Konnte Test-Position zum Schließen nicht finden.")

            logger.warning(f"[{account_name}] TEST-MODUS ERFOLGREICH ABGESCHLOSSEN.")
            return

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
                data = bitget.fetch_recent_ohlcv(SYMBOL, params['market']['timeframe'])
                data = calculate_smc_indicators(data, params['strategy'])
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
                    bitget.place_trailing_stop_order(SYMBOL, close_side, contracts, callback_rate, current_price, params={'reduceOnly': True})
                    set_state(account_name, 'trailing_stop_active', 'True')
                    message = f"🚀 Trailing Stop für *{account_name}* ({SYMBOL}) aktiviert!\n- Gewinn ist bei >{activation_rr}:1 gesichert."
                    send_telegram_message(bot_token, chat_id, message)
            else:
                logger.info(f"[{account_name}] Trailing Stop ist bereits aktiv. Management durch Bitget.")
            return

        if get_state(account_name, 'trailing_stop_active') == 'True':
            set_state(account_name, 'trailing_stop_active', 'False')

        if bitget.fetch_open_orders(SYMBOL):
            logger.info(f"[{account_name}] Warte auf Ausführung der Limit-Order für {SYMBOL}.")
            return

        logger.info(f"[{account_name}] Keine Position offen. Suche nach neuen SMC-Einstiegen.")
        bitget.cancel_all_orders(SYMBOL)
        bitget.cancel_all_trigger_orders(SYMBOL)
        
        data = bitget.fetch_recent_ohlcv(SYMBOL, params['market']['timeframe'])
        data = calculate_smc_indicators(data, params['strategy'])
        latest = data.iloc[-2]

        if not np.isnan(latest['bos_level']) and not np.isnan(latest['ob_high']):
            signal_ts = int(latest.name.timestamp())
            last_signal_ts = int(get_state(account_name, 'last_signal_ts'))

            if signal_ts > last_signal_ts:
                entry_price, stop_loss_price, side = None, None, None
                if latest['trend'] == 1 and params['behavior']['use_longs'] and latest['low'] > latest['ob_high']:
                    entry_price, stop_loss_price, side = latest['ob_high'], latest['ob_low'], 'buy'
                elif latest['trend'] == -1 and params['behavior']['use_shorts'] and latest['high'] < latest['ob_low']:
                    entry_price, stop_loss_price, side = latest['ob_low'], latest['ob_high'], 'sell'
                
                if side:
                    leverage, margin_mode = params['risk']['leverage'], params['risk']['margin_mode']
                    risk_per_trade_pct = params['risk']['risk_per_trade_pct'] / 100
                    balance = bitget.fetch_balance().get('USDT', {}).get('free', 0) * (params['risk']['balance_fraction_pct'] / 100)
                    risk_per_trade_usd = balance * risk_per_trade_pct
                    sl_distance = abs(entry_price - stop_loss_price)
                    if sl_distance == 0:
                        logger.warning(f"[{account_name}] SL-Distanz ist 0, Trade übersprungen."); return
                    
                    amount_calculated = risk_per_trade_usd / sl_distance
                    market_info = bitget.get_market_info(SYMBOL)
                    min_cost = market_info.get('limits', {}).get('cost', {}).get('min', 5.0)
                    if (amount_calculated * entry_price) < min_cost:
                        logger.warning(f"[{account_name}] Berechnete Ordergröße ({amount_calculated * entry_price:.2f} USDT) unter Minimum. Erhöhe auf {min_cost:.2f} USDT.")
                        amount_calculated = (min_cost * 1.05) / entry_price
                    amount = amount_calculated

                    rr = params['risk']['risk_reward_ratio']
                    take_profit_price = entry_price + sl_distance * rr if side == 'buy' else entry_price - sl_distance * rr
                    close_side = 'sell' if side == 'buy' else 'buy'
                    
                    bitget.set_margin_mode(SYMBOL, margin_mode)
                    bitget.set_leverage(SYMBOL, leverage, margin_mode)
                    bitget.place_limit_order(SYMBOL, side, amount, entry_price, params={'postOnly': True})
                    bitget.place_trigger_market_order(SYMBOL, close_side, amount, take_profit_price, params={'reduceOnly': True})
                    bitget.place_trigger_market_order(SYMBOL, close_side, amount, stop_loss_price, params={'reduceOnly': True})
                    
                    logger.info(f"[{account_name}] TitanBot Signal! Entry, TP und SL Orders platziert.")
                    set_state(account_name, 'last_signal_ts', signal_ts)
                    message = f"📈 TitanBot Signal für Account *{account_name}* ({SYMBOL}, {side.upper()})\n- Order @ ${entry_price:.4f}\n- SL: ${stop_loss_price:.4f}\n- TP: ${take_profit_price:.4f}"
                    send_telegram_message(bot_token, chat_id, message)

    except Exception as e:
        logger.error(f"[{account_name}] Ein unerwarteter Fehler ist aufgetreten: {e}", exc_info=True)
        error_message = f"🚨 KRITISCHER FEHLER im TitanBot für Account *{account_name}* ({SYMBOL})!\n\n`{traceback.format_exc()}`"
        send_telegram_message(bot_token, chat_id, error_message[:4000])
    
    logger.info(f"--- Ausführung für Account: {account_name} abgeschlossen ---")

def main():
    logger.info(">>> TitanBot wird gestartet <<<")
    try:
        key_path = os.path.abspath(os.path.join(PROJECT_ROOT, 'secret.json'))
        with open(key_path, "r") as f: secrets = json.load(f)
        
        api_configs = secrets.get('titan')
        if not api_configs:
            logger.critical("Fehler: Kein 'titan' Eintrag in secret.json gefunden.")
            return
            
        telegram_config = secrets.get('telegram', {})
        
        if isinstance(api_configs, list):
            logger.info(f"Multi-Account-Modus erkannt. Verarbeite {len(api_configs)} Accounts.")
            accounts_to_run = api_configs
        elif isinstance(api_configs, dict):
            logger.info("Single-Account-Modus erkannt.")
            accounts_to_run = [api_configs]
        else:
            logger.critical("Fehler: Der 'titan' Eintrag in secret.json hat ein ungültiges Format.")
            return

    except Exception as e:
        logger.critical(f"Fehler beim Laden der API-Schlüssel oder Konfiguration: {e}")
        sys.exit(1)

    for account in accounts_to_run:
        api_key = account.get('apiKey')
        secret = account.get('secret')
        password = account.get('password')
        account_name = account.get('name', 'Unbenannter Account')

        if not api_key or not secret or not password:
            logger.warning(f"Überspringe Account '{account_name}', da API-Schlüssel, Secret oder Passwort fehlen.")
            continue
        
        try:
            run_for_account(account, telegram_config)
        except Exception as e:
            logger.error(f"Schwerwiegender Fehler bei der Ausführung für Account {account_name}: {e}", exc_info=True)

    logger.info(">>> TitanBot-Lauf abgeschlossen <<<\n")

if __name__ == "__main__":
    main()
