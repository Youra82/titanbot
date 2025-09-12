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
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s UTC %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
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

# ========================================
# Haupt-Logik pro Account
# ========================================
def run_for_account(account, telegram_config):
    account_name = account.get('name', 'Standard-Account')
    bot_token = telegram_config.get('bot_token')
    chat_id = telegram_config.get('chat_id')

    logger.info(f"--- Starte TitanBot Ausführung für Account: {account_name} | Symbol: {SYMBOL} ---")

    bitget = BitgetFutures(account)
    setup_database(account_name)

    try:
        # --------------------
        # TESTMODUS: sofortiger Trade
        # --------------------
        if params.get('debug', {}).get('test_mode', False):
            logger.warning(f"[{account_name}] ACHTUNG: TEST-MODUS IST AKTIV!")

            margin_mode = params['risk']['margin_mode']
            leverage = params['risk']['leverage']

            logger.info(f"[{account_name}] Setze Margin-Mode='{margin_mode}'...")
            bitget.set_margin_mode(SYMBOL, margin_mode)
            logger.info(f"[{account_name}] Setze Leverage={leverage}...")
            bitget.set_leverage(SYMBOL, leverage)

            # Marktdaten holen
            ticker = bitget.exchange.fetch_ticker(SYMBOL)
            current_price = ticker.get('last')
            if not current_price or current_price <= 0:
                logger.error(f"[{account_name}] Ungültiger Preis vom Ticker. Abbruch Testmodus.")
                return

            # Tradegröße berechnen (hier einfach 10 USDT als Test)
            amount = 10 / current_price

            entry_price = current_price
            sl_price = entry_price * 0.99  # 1% unter Entry
            tp_price = entry_price * 1.03  # 3% über Entry
            side = 'buy'

            # Entry + SL + TP
            bitget.place_limit_order(SYMBOL, side, amount, entry_price)
            time.sleep(1)
            bitget.place_stop_loss_order(SYMBOL, side, amount, sl_price)
            time.sleep(1)
            bitget.place_take_profit_order(SYMBOL, side, amount, tp_price)

            logger.info(f"[{account_name}] TEST-TRADE platziert: Entry={entry_price:.4f}, SL={sl_price:.4f}, TP={tp_price:.4f}")
            return

        # --------------------
        # Normale Bot-Logik (Position prüfen, SMC-Signale)
        # --------------------
        # Hier bleibt deine komplette bestehende Logik aus dem Originalrun.py erhalten:
        # - fetch_open_positions
        # - Trailing Stop Management
        # - neue SMC-Signale prüfen
        # - Limit + TP + SL Orders setzen
        # - Telegram Benachrichtigungen
        # --------------------
        # (Hier kannst du einfach deinen bisherigen Codeblock für normale Positionen einfügen)
        pass  # Platzhalter für Originallogik

    except Exception as e:
        logger.error(f"[{account_name}] Ein unerwarteter Fehler ist aufgetreten: {e}", exc_info=True)
        error_message = f"🚨 KRITISCHER FEHLER im TitanBot für Account *{account_name}* ({SYMBOL})!\n\n`{traceback.format_exc()}`"
        send_telegram_message(bot_token, chat_id, error_message[:4000])

    logger.info(f"--- Ausführung für Account: {account_name} abgeschlossen ---")

# ========================================
# Haupt-Entry
# ========================================
def main():
    logger.info(">>> TitanBot wird gestartet <<<")
    try:
        key_path = os.path.abspath(os.path.join(PROJECT_ROOT, 'secret.json'))
        with open(key_path, "r") as f:
            secrets = json.load(f)

        api_configs = secrets.get('titan')
        telegram_config = secrets.get('telegram', {})

        if isinstance(api_configs, list):
            accounts_to_run = api_configs
        elif isinstance(api_configs, dict):
            accounts_to_run = [api_configs]
        else:
            logger.critical("Ungültiges Format im secret.json")
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
