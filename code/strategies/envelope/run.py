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

# --- Lade Konfiguration ---
def load_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        logging.critical(f"Kritischer Fehler: Lade config.json: {e}")
        sys.exit(1)

CONFIG = load_config()

# --- Logging einrichten ---
LOG_DIR = os.path.join(PROJECT_ROOT, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'titanbot.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s UTC: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
logger = logging.getLogger('titan_bot')

# --- Globale Variablen & Helfer ---
try:
    with open(os.path.join(PROJECT_ROOT, 'secret.json'), "r") as f:
        secrets = json.load(f)
    API_SETUP = secrets.get('envelope', secrets.get('bitget_example')) # Flexibler Key-Zugriff
    TELEGRAM_BOT_TOKEN = secrets.get('telegram', {}).get('bot_token')
    TELEGRAM_CHAT_ID = secrets.get('telegram', {}).get('chat_id')
except Exception as e:
    logger.critical(f"Kritischer Fehler beim Laden der Keys: {e}")
    sys.exit(1)

DB_PATH = os.path.join(os.path.dirname(__file__), f"titanbot_tracker.db")
state_manager = StateManager(DB_PATH)
bitget = BitgetFutures(API_SETUP)

# Zuordnung von Nummer zu Funktion und Name
STRATEGY_MAPPING = {
    1: ("momentum_accelerator", calculate_momentum_signals),
    2: ("volatility_catcher", calculate_volatility_signals),
    3: ("tidal_wave_rider", calculate_tidal_wave_signals)
}

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, data=payload, timeout=10)
    except requests.exceptions.RequestException as e:
        logger.error(f"Fehler bei Telegram: {e}")

# --- Kern-Funktionen ---
def get_active_strategy():
    """Liest die Konfiguration aus und gibt die aktive Strategie und deren Parameter zurück."""
    try:
        strategy_num = CONFIG["_HEADING_STEP_1_"]["active_strategy_number"]
        strategy_name, signal_func = STRATEGY_MAPPING[strategy_num]
        
        strategy_params = CONFIG["_HEADING_STEP_2_"]["strategies"][strategy_name]
        global_params = CONFIG["_HEADING_STEP_3_"]["global_settings"]
        
        return strategy_name, signal_func, strategy_params, global_params
    except (KeyError, TypeError):
        logger.critical(f"Fehler in der config.json Struktur oder ungültige Strategienummer.")
        sys.exit(1)

def open_position(side, signal_candle, strategy_name, strategy_params, global_params):
    """
    Eröffnet eine neue Position und implementiert die Logik für
    "Intelligenten SL" und "Comeback TP".
    """
    try:
        symbol = global_params['symbol']
        leverage = global_params['leverage']
        state = state_manager.get_state()
        verlust_vortrag = state.get('verlust_vortrag', 0.0)

        # 1. Handelsgröße berechnen
        balance = bitget.fetch_balance().get('USDT', {}).get('total', 0.0)
        trade_size_margin = balance * (global_params['trade_size_pct'] / 100)
        current_price = bitget.fetch_ticker(symbol)['last']
        amount = (trade_size_margin * leverage) / current_price

        if trade_size_margin * leverage < 5.0: # Mindesthandelsvolumen
            logger.error(f"Handelsgröße zu gering ({trade_size_margin * leverage:.2f} USDT).")
            return

        # 2. "Comeback TP" berechnen
        standard_tp_price = signal_candle['tp_price']
        standard_profit_per_unit = abs(standard_tp_price - current_price)
        verlust_vortrag_per_unit = verlust_vortrag / amount if amount > 0 else 0
        
        final_tp_price = (current_price + standard_profit_per_unit + verlust_vortrag_per_unit) if side == 'long' \
            else (current_price - standard_profit_per_unit - verlust_vortrag_per_unit)

        # 3. Orders platzieren
        logger.info(f"Öffne {side.upper()}-Position für {symbol}...")
        bitget.place_market_order(symbol, 'buy' if side == 'long' else 'sell', amount)
        bitget.place_limit_order(
            symbol, 'sell' if side == 'long' else 'buy', amount, final_tp_price, reduce=True
        )
        sl_price = signal_candle['sl_price']
        sl_order = bitget.place_trigger_market_order(
            symbol, 'sell' if side == 'long' else 'buy', amount, sl_price
        )
        
        # 4. Status speichern
        state_manager.set_state(
            status="in_trade", last_side=side, stop_loss_order_id=sl_order['id'],
            entry_price=current_price, position_amount=amount
        )

        msg = (f"✅ *Neue Position (Titanbot)*\n"
               f"Strategie: *{strategy_name.replace('_', ' ').title()}*\n"
               f"Symbol: *{symbol}*\n"
               f"Seite: *{side.upper()}* @ {current_price:.4f}\n"
               f"Take-Profit: {final_tp_price:.4f} (Vortrag: {verlust_vortrag:.2f} USDT)\n"
               f"Intelligenter SL: {sl_price:.4f}")
        logger.info(msg.replace('*', ''))
        send_telegram_message(msg)

    except Exception as e:
        logger.error(f"Fehler beim Eröffnen der Position: {e}", exc_info=True)
        send_telegram_message(f"❌ Fehler bei Positionseröffnung: {e}")
        state_manager.reset_trade_state()


def manage_position(global_params):
    """
    Überwacht die Trigger-Order des "Intelligenten Stop-Loss".
    Wenn sie ausgelöst wird, wird die Hauptposition geschlossen.
    """
    try:
        state = state_manager.get_state()
        sl_order_id = state.get('stop_loss_order_id')
        symbol = global_params['symbol']

        open_trigger_orders = bitget.fetch_open_trigger_orders(symbol)
        sl_order_found = any(o['id'] == sl_order_id for o in open_trigger_orders)

        if not sl_order_found:
            logger.warning(f"Intelligenter SL (Order {sl_order_id}) wurde ausgelöst! Schließe Hauptposition.")
            bitget.flash_close_position(symbol)
            
            closed_price = bitget.fetch_ticker(symbol)['last']
            entry_price = state.get('entry_price', closed_price)
            amount = state.get('position_amount', 0)
            leverage = global_params['leverage']
            
            pnl_pct = (closed_price / entry_price - 1) if state['last_side'] == 'long' else (1 - closed_price / entry_price)
            estimated_loss = abs(pnl_pct * entry_price * amount * leverage)
            
            new_verlust_vortrag = state.get('verlust_vortrag', 0.0) + estimated_loss
            new_loss_count = state.get('consecutive_loss_count', 0) + 1
            
            state_manager.set_state(
                verlust_vortrag=new_verlust_vortrag,
                consecutive_loss_count=new_loss_count
            )
            
            msg = (f"🛑 *STOP-LOSS (Titanbot)*\n"
                   f"Symbol: *{symbol}*\n"
                   f"Position wurde durch intelligenten SL geschlossen.\n"
                   f"Neuer Verlustvortrag: {new_verlust_vortrag:.2f} USDT")
            logger.info(msg.replace('*', ''))
            send_telegram_message(msg)
            
            state_manager.reset_trade_state()
        else:
            logger.info(f"Position aktiv. Überwache intelligenten SL (Order {sl_order_id}).")

    except Exception as e:
        logger.error(f"Fehler beim Management der Position: {e}", exc_info=True)
        send_telegram_message(f"❌ Fehler im Positions-Management: {e}")


# --- Haupt-Schleife ---
if __name__ == "__main__":
    strategy_name, signal_func, strategy_params, global_params = get_active_strategy()
    
    logger.info(f">>> Starte Titanbot-Lauf für {global_params['symbol']} mit Strategie '{strategy_name}' <<<")
    
    try:
        positions = bitget.fetch_open_positions(global_params['symbol'])
        is_position_open = len(positions) > 0
        state = state_manager.get_state()

        if not is_position_open and state['status'] != 'ok_to_trade':
            logger.warning("Bot-Status war 'in_trade', aber keine Position gefunden. Setze zurück.")
            send_telegram_message(f"ℹ️ *Info ({global_params['symbol']}):* Position extern geschlossen. Bot-Status zurückgesetzt.")
            state_manager.reset_trade_state()
            state = state_manager.get_state()

        if is_position_open:
            manage_position(global_params)
        else:
            if state.get('consecutive_loss_count', 0) >= global_params.get('loss_account_max_strikes', 3):
                logger.info(f"Verlust-Konto wird nach {state['consecutive_loss_count']} Verlusten zurückgesetzt.")
                send_telegram_message(f"ℹ️ *Info ({global_params['symbol']}):* Verlust-Konto zurückgesetzt.")
                state_manager.set_state(verlust_vortrag=0.0, consecutive_loss_count=0)

            data = bitget.fetch_recent_ohlcv(global_params['symbol'], global_params['timeframe'], limit=100)
            data_with_signals = signal_func(data.copy(), strategy_params)
            last_candle = data_with_signals.iloc[-2]

            if last_candle.get('buy_signal', False):
                logger.info(f"Neues LONG-Signal auf Basis von '{strategy_name}' erkannt.")
                open_position('long', last_candle, strategy_name, strategy_params, global_params)
            elif last_candle.get('sell_signal', False):
                logger.info(f"Neues SHORT-Signal auf Basis von '{strategy_name}' erkannt.")
                open_position('short', last_candle, strategy_name, strategy_params, global_params)
            else:
                logger.info("Warte auf Signal...")

    except Exception as e:
        logger.error(f"FATALER FEHLER in der Hauptschleife: {e}", exc_info=True)
        send_telegram_message(f"🚨 *FATALER FEHLER (Titanbot):* {e}")
        
    logger.info(">>> Titanbot-Lauf abgeschlossen <<<\n")

