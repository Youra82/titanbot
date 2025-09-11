# code/strategies/envelope/run.py

import os
import sys
import json
import logging
import traceback
import sqlite3
import numpy as np

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
    # Erstellt einen sicheren Dateinamen aus dem Account-Namen
    safe_account_name = "".join(c for c in account_name if c.isalnum() or c in (' ', '_')).rstrip()
    return os.path.join(os.path.dirname(__file__), f"titan_state_{safe_account_name}_{SYMBOL.replace('/', '-')}.db")

def setup_database(account_name):
    db_file = get_db_file_path(account_name)
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value TEXT)''')
    cursor.execute("INSERT OR IGNORE INTO bot_state (key, value) VALUES (?, ?)", ('last_signal_ts', '0'))
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
    # Nutzt den 'name' aus der Config, oder einen Standardwert, falls nicht vorhanden
    account_name = account.get('name', 'Standard-Account')
    bot_token = telegram_config.get('bot_token')
    chat_id = telegram_config.get('chat_id')
    
    logger.info(f"--- Starte TitanBot Ausführung für Account: {account_name} | Symbol: {SYMBOL} ---")
    
    bitget = BitgetFutures(account)
    setup_database(account_name)

    try:
        if bitget.fetch_open_positions(SYMBOL):
            logger.info(f"[{account_name}] Position für {SYMBOL} ist offen. Management durch TitanBot.")
            return
        if bitget.fetch_open_orders(SYMBOL):
            logger.info(f"[{account_name}] Warte auf Ausführung der Limit-Order für {SYMBOL}.")
            return

        logger.info(f"[{account_name}] Keine Position offen. Suche nach neuen SMC-Einstiegen.")
        bitget.cancel_all_orders(SYMBOL)
        
        data = bitget.fetch_recent_ohlcv(SYMBOL, params['market']['timeframe'], 500)
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
                    risk_per_trade_pct = params['risk']['risk_per_trade_pct'] / 100
                    balance = bitget.fetch_balance().get('USDT', {}).get('free', 0) * (params['risk']['balance_fraction_pct'] / 100)
                    risk_per_trade_usd = balance * risk_per_trade_pct
                    sl_distance = abs(entry_price - stop_loss_price)
                    if sl_distance == 0:
                        logger.warning(f"[{account_name}] SL-Distanz ist 0, Trade übersprungen."); return
                    
                    amount = risk_per_trade_usd / sl_distance
                    leverage, margin_mode = params['risk']['leverage'], params['risk']['margin_mode']
                    rr = params['risk']['risk_reward_ratio']
                    take_profit_price = entry_price + sl_distance * rr if side == 'buy' else entry_price - sl_distance * rr

                    bitget.set_margin_mode(SYMBOL, margin_mode)
                    bitget.set_leverage(SYMBOL, leverage, margin_mode)
                    logger.info(f"[{account_name}] TitanBot Signal! Platziere Limit-Order: {amount:.4f} {SYMBOL.split('/')[0]} @ ${entry_price:.4f}")
                    bitget.place_limit_order(SYMBOL, side, amount, entry_price, leverage, margin_mode)
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
        
        api_configs = secrets['titan']
        telegram_config = secrets.get('telegram', {})
        
        # Prüft, ob es sich um eine Liste (Multi-Account) oder ein einzelnes Objekt (Single-Account) handelt
        if isinstance(api_configs, list):
            logger.info(f"Multi-Account-Modus erkannt. Verarbeite {len(api_configs)} Accounts.")
            accounts_to_run = api_configs
        elif isinstance(api_configs, dict):
            logger.info("Single-Account-Modus erkannt.")
            accounts_to_run = [api_configs] # Verpackt das einzelne Objekt in eine Liste für die Schleife
        else:
            logger.critical("Fehler: Der 'titan' Eintrag in secret.json hat ein ungültiges Format.")
            return

    except Exception as e:
        logger.critical(f"Fehler beim Laden der API-Schlüssel oder Konfiguration: {e}")
        sys.exit(1)

    # Iteriert durch die Liste der Accounts (egal ob einer oder mehrere)
    for account in accounts_to_run:
        try:
            run_for_account(account, telegram_config)
        except Exception as e:
            logger.error(f"Schwerwiegender Fehler bei der Ausführung für Account {account.get('name', 'Unbekannt')}: {e}", exc_info=True)

    logger.info(">>> TitanBot-Lauf abgeschlossen <<<\n")

if __name__ == "__main__":
    main()
