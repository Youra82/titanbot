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

# ... (Alle Datenbank-Funktionen bleiben unverändert) ...

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

            # --- START DER ÄNDERUNG: Parameter direkt erstellen ---
            margin_mode = params['risk']['margin_mode']
            leverage = params['risk']['leverage']
            order_params = {'leverage': leverage, 'marginMode': margin_mode}
            logger.info(f"[{account_name}] Test-Parameter vorbereitet: {order_params}")

            market_info = bitget.get_market_info(SYMBOL)
            min_cost = market_info.get('limits', {}).get('cost', {}).get('min', 5.0)
            
            ticker = bitget.fetch_ticker(SYMBOL)
            current_price = ticker.get('last')
            
            if not current_price or current_price <= 0:
                logger.error(f"[{account_name}] Ungültiger Preis ({current_price}) vom Ticker erhalten. Test-Modus wird abgebrochen.")
                return
            
            target_cost = min_cost * 1.1
            
            logger.info(f"[{account_name}] Platziere Test-Market-BUY-Order im Wert von ~{target_cost:.2f} USDT...")
            # Parameter werden hier direkt übergeben
            buy_order = bitget.create_market_buy_order_with_cost(SYMBOL, target_cost, params=order_params)
            logger.info(f"[{account_name}] Test-BUY-Order {buy_order['id']} erfolgreich platziert.")
            
            time.sleep(3) 

            open_pos = bitget.fetch_open_positions(SYMBOL)
            if open_pos:
                contracts_to_close = float(open_pos[0]['contracts'])
                logger.info(f"[{account_name}] Schließe Test-Position (Menge: {contracts_to_close:.4f})...")
                # Parameter werden auch hier direkt übergeben
                sell_order = bitget.create_market_order(SYMBOL, 'sell', contracts_to_close, {'reduceOnly': True, **order_params})
                logger.info(f"[{account_name}] Test-Position mit Order {sell_order['id']} erfolgreich geschlossen.")
            else:
                 logger.warning(f"[{account_name}] Konnte Test-Position zum Schließen nicht finden.")

            logger.warning(f"[{account_name}] TEST-MODUS ERFOLGREICH ABGESCHLOSSEN.")
            # --- ENDE DER ÄNDERUNG ---
            return

        position = bitget.fetch_open_positions(SYMBOL)
        
        if position:
            # ... (Logik für offene Positionen bleibt unverändert) ...
            return

        if get_state(account_name, 'trailing_stop_active') == 'True':
            set_state(account_name, 'trailing_stop_active', 'False')

        if bitget.fetch_open_orders(SYMBOL):
            logger.info(f"[{account_name}] Warte auf Ausführung der Limit-Order für {SYMBOL}.")
            return

        logger.info(f"[{account_name}] Keine Position offen. Suche nach neuen SMC-Einstiegen.")
        bitget.cancel_all_orders(SYMBOL)
        bitget.cancel_all_trigger_orders(SYMBOL)
        
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
                    # --- START DER ÄNDERUNG: Parameter direkt erstellen ---
                    leverage, margin_mode = params['risk']['leverage'], params['risk']['margin_mode']
                    order_params = {'leverage': leverage, 'marginMode': margin_mode}
                    
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

                    # Die alten set_margin_mode und set_leverage Aufrufe werden entfernt
                    
                    # Parameter werden hier direkt übergeben
                    bitget.place_limit_order(SYMBOL, side, amount, entry_price, params={'postOnly': True, **order_params})
                    bitget.place_trigger_market_order(SYMBOL, close_side, amount, take_profit_price, {'reduceOnly': True})
                    bitget.place_trigger_market_order(SYMBOL, close_side, amount, stop_loss_price, {'reduceOnly': True})
                    
                    logger.info(f"[{account_name}] TitanBot Signal! Entry, TP und SL Orders platziert mit Hebel {leverage}x.")
                    # --- ENDE DER ÄNDERUNG ---
                    
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
