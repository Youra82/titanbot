import os
import sys
import json
import time
import logging
import requests
import pandas as pd

# --- Systempfade und Initialisierung ---
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(PROJECT_ROOT)

from utilities.bitget_futures import BitgetFutures
from utilities.strategy_logic import calculate_momentum_signals, calculate_volatility_signals, calculate_tidal_wave_signals
from utilities.state_manager import StateManager

def load_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    try:
        with open(config_path, 'r') as f: return json.load(f)
    except Exception as e:
        logging.critical(f"Kritischer Fehler: Lade config.json: {e}"); sys.exit(1)

CONFIG = load_config()

# --- Logging einrichten ---
LOG_DIR = os.path.join(PROJECT_ROOT, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'titanbot.log')
logging.basicConfig(level=logging.INFO, format='%(asctime)s UTC: %(message)s', datefmt='%Y-%m-%d %H:%M:%S', handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
logger = logging.getLogger('titan_bot')

# --- Globale Variablen & Helfer ---
try:
    with open(os.path.join(PROJECT_ROOT, 'secret.json'), "r") as f: secrets = json.load(f)
    API_SETUP = secrets.get('envelope', secrets.get('bitget_example'))
    TELEGRAM_BOT_TOKEN = secrets.get('telegram', {}).get('bot_token')
    TELEGRAM_CHAT_ID = secrets.get('telegram', {}).get('chat_id')
except Exception as e:
    logger.critical(f"Kritischer Fehler beim Laden der Keys: {e}"); sys.exit(1)

DB_PATH = os.path.join(os.path.dirname(__file__), f"titanbot_tracker.db")
state_manager = StateManager(DB_PATH)
bitget = BitgetFutures(API_SETUP)

STRATEGY_MAPPING = {
    1: ("momentum_accelerator", calculate_momentum_signals),
    2: ("volatility_catcher", calculate_volatility_signals),
    3: ("tidal_wave_rider", calculate_tidal_wave_signals)
}

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, data=payload, timeout=10)
    except requests.exceptions.RequestException as e:
        logger.error(f"Fehler bei Telegram: {e}")

def get_active_strategy():
    try:
        cfg1 = CONFIG["_HEADING_STEP_1_"]
        cfg2 = CONFIG["_HEADING_STEP_2_"]
        cfg3 = CONFIG["_HEADING_STEP_3_"]
        cfg4 = CONFIG["_HEADING_STEP_4_"]
        
        strategy_num = cfg1["active_strategy_number"]
        strategy_name, signal_func = STRATEGY_MAPPING[strategy_num]
        
        strategy_params = cfg2["strategies"][strategy_name]
        global_params = cfg3["global_settings"]
        risk_params = cfg4["risk_management"]
        
        return strategy_name, signal_func, strategy_params, global_params, risk_params
    except Exception as e:
        logger.critical(f"Fehler in der config.json Struktur: {e}"); sys.exit(1)

def open_position(side, signal_candle, strategy_name, strategy_params, global_params, risk_params):
    try:
        symbol = global_params['symbol']
        state = state_manager.get_state()
        verlust_vortrag = state.get('verlust_vortrag', 0.0)
        risk_per_trade_pct = risk_params['risk_per_trade_pct'] / 100

        balance = bitget.fetch_balance().get('USDT', {}).get('total', 0.0)
        current_price = bitget.fetch_ticker(symbol)['last']
        sl_price = signal_candle['sl_price']
        
        risk_amount_usd = balance * risk_per_trade_pct
        stop_loss_distance_pct = abs(current_price - sl_price) / current_price if current_price > 0 else 0
        if stop_loss_distance_pct == 0:
            logger.error("Stop-Loss Distanz ist Null. Trade wird übersprungen."); return
        
        position_size_usd = risk_amount_usd / stop_loss_distance_pct
        amount = position_size_usd / current_price
        effective_leverage = position_size_usd / balance if balance > 0 else 0

        standard_tp_price = signal_candle['tp_price']
        standard_profit_per_unit = abs(standard_tp_price - current_price)
        verlust_vortrag_per_unit = verlust_vortrag / amount if amount > 0 else 0
        final_tp_price = (current_price + standard_profit_per_unit + verlust_vortrag_per_unit) if side == 'long' \
            else (current_price - standard_profit_per_unit - verlust_vortrag_per_unit)

        logger.info(f"Öffne {side.upper()}-Position für {symbol}...")
        bitget.place_market_order(symbol, 'buy' if side == 'long' else 'sell', amount)
        bitget.place_limit_order(symbol, 'sell' if side == 'long' else 'buy', amount, final_tp_price, reduce=True)
        sl_order = bitget.place_trigger_market_order(symbol, 'sell' if side == 'long' else 'buy', amount, sl_price)
        
        state_manager.set_state(status="in_trade", last_side=side, stop_loss_order_id=sl_order['id'], entry_price=current_price, risk_amount_usd=risk_amount_usd)

        msg = (f"✅ *Neue Position (Titanbot)*\n"
               f"Strategie: *{strategy_name.replace('_', ' ').title()}*\n"
               f"Symbol: *{symbol}*\n"
               f"Seite: *{side.upper()}* @ {current_price:.4f}\n"
               f"Effektiver Hebel: *{effective_leverage:.2f}x*\n"
               f"Take-Profit: {final_tp_price:.4f}\n"
               f"Stop-Loss: {sl_price:.4f} (Risiko: {risk_amount_usd:.2f} USDT)")
        logger.info(msg.replace('*', '')); send_telegram_message(msg)

    except Exception as e:
        logger.error(f"Fehler bei Positionseröffnung: {e}", exc_info=True); send_telegram_message(f"❌ Fehler bei Positionseröffnung: {e}"); state_manager.reset_trade_state()

def manage_position(global_params):
    try:
        state = state_manager.get_state()
        sl_order_id = state.get('stop_loss_order_id')
        symbol = global_params['symbol']

        open_trigger_orders = bitget.fetch_open_trigger_orders(symbol)
        sl_order_found = any(o['id'] == sl_order_id for o in open_trigger_orders)

        if not sl_order_found:
            logger.warning(f"Intelligenter SL (Order {sl_order_id}) wurde ausgelöst! Schließe Position.")
            bitget.flash_close_position(symbol)
            
            lost_amount = state.get('risk_amount_usd', 0.0)
            new_verlust_vortrag = state.get('verlust_vortrag', 0.0) + lost_amount
            new_loss_count = state.get('consecutive_loss_count', 0) + 1
            
            state_manager.set_state(verlust_vortrag=new_verlust_vortrag, consecutive_loss_count=new_loss_count)
            
            msg = (f"🛑 *STOP-LOSS (Titanbot)*\n"
                   f"Symbol: *{symbol}*\n"
                   f"Neuer Verlustvortrag: {new_verlust_vortrag:.2f} USDT")
            logger.info(msg.replace('*', '')); send_telegram_message(msg)
            
            state_manager.reset_trade_state()
        else:
            logger.info(f"Position aktiv. Überwache intelligenten SL (Order {sl_order_id}).")

    except Exception as e:
        logger.error(f"Fehler im Positions-Management: {e}", exc_info=True); send_telegram_message(f"❌ Fehler im Positions-Management: {e}")

if __name__ == "__main__":
    strategy_name, signal_func, strategy_params, global_params, risk_params = get_active_strategy()
    
    logger.info(f">>> Starte Titanbot-Lauf für {global_params['symbol']} mit Strategie '{strategy_name}' <<<")
    
    try:
        positions = bitget.fetch_open_positions(global_params['symbol'])
        is_position_open = len(positions) > 0
        state = state_manager.get_state()
        
        if not is_position_open:
            # +++ HEBEL WIRD AUTOMATISCH GESETZT +++
            logger.info("Keine Position offen. Setze Hebel auf 25x als technische Obergrenze.")
            bitget.set_margin_mode(global_params['symbol'], global_params['margin_mode'])
            bitget.set_leverage(global_params['symbol'], 25) # Setzt ein hohes Tempolimit
            
            if state['status'] != 'ok_to_trade':
                logger.warning("Bot-Status war 'in_trade', aber keine Position gefunden. Setze zurück.")
                send_telegram_message(f"ℹ️ *Info ({global_params['symbol']}):* Position extern geschlossen. Bot-Status zurückgesetzt.")
                state_manager.reset_trade_state()
                state = state_manager.get_state()

            if state.get('consecutive_loss_count', 0) >= global_params.get('loss_account_max_strikes', 3):
                logger.info(f"Verlust-Konto wird nach {state['consecutive_loss_count']} Verlusten zurückgesetzt.")
                send_telegram_message(f"ℹ️ *Info ({global_params['symbol']}):* Verlust-Konto zurückgesetzt.")
                state_manager.set_state(verlust_vortrag=0.0, consecutive_loss_count=0)

            data = bitget.fetch_recent_ohlcv(global_params['symbol'], global_params['timeframe'], limit=100)
            data_with_signals = signal_func(data.copy(), strategy_params)
            last_candle = data_with_signals.iloc[-2]

            if last_candle.get('buy_signal', False):
                logger.info(f"Neues LONG-Signal auf Basis von '{strategy_name}' erkannt.")
                open_position('long', last_candle, strategy_name, strategy_params, global_params, risk_params)
            elif last_candle.get('sell_signal', False):
                logger.info(f"Neues SHORT-Signal auf Basis von '{strategy_name}' erkannt.")
                open_position('short', last_candle, strategy_name, strategy_params, global_params, risk_params)
            else:
                logger.info("Warte auf Signal...")
        else:
            manage_position(global_params)

    except Exception as e:
        logger.error(f"FATALER FEHLER in der Hauptschleife: {e}", exc_info=True); send_telegram_message(f"🚨 *FATALER FEHLER (Titanbot):* {e}")
        
    logger.info(">>> Titanbot-Lauf abgeschlossen <<<\n")
