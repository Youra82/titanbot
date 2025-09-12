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

# ... (Datenbank-Funktionen bleiben unverändert) ...

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

            # --- START DER KORREKTUR: Explizite Reihenfolge ---
            margin_mode = params['risk']['margin_mode']
            leverage = params['risk']['leverage']
            
            logger.info(f"[{account_name}] Setze Margin-Modus auf '{margin_mode}'...")
            bitget.set_margin_mode(SYMBOL, margin_mode)

            logger.info(f"[{account_name}] Setze Hebel auf {leverage}x...")
            bitget.set_leverage(SYMBOL, leverage)
            
            market_info = bitget.get_market_info(SYMBOL)
            min_cost = market_info.get('limits', {}).get('cost', {}).get('min', 5.0)
            target_cost = min_cost * 1.1 
            
            logger.info(f"[{account_name}] Platziere Test-Market-BUY-Order im Wert von ~{target_cost:.2f} USDT...")
            # create_market_buy_order_with_cost ist nicht für Futures -> manuelle Berechnung
            ticker = bitget.fetch_ticker(SYMBOL)
            current_price = ticker.get('last')
            if not current_price or current_price <= 0:
                logger.error(f"Ungültiger Preis für Benchmark erhalten: {current_price}")
                return
            amount = target_cost / current_price
            buy_order = bitget.create_market_order(SYMBOL, 'buy', amount)
            # --- ENDE DER KORREKTUR ---
            
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
        
        # ... (Die gesamte normale Handelslogik bleibt hier unverändert) ...

    except Exception as e:
        logger.error(f"[{account_name}] Ein unerwarteter Fehler ist aufgetreten: {e}", exc_info=True)
        # ... (Fehlerbehandlung bleibt unverändert) ...
    
    logger.info(f"--- Ausführung für Account: {account_name} abgeschlossen ---")

def main():
    # ... (Die main-Funktion bleibt unverändert) ...

if __name__ == "__main__":
    main()
