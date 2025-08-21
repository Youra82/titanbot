# code/strategies/envelope/run.py
import os, sys, json, time, logging, requests, pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(PROJECT_ROOT)

from utilities.bitget_futures import BitgetFutures
from utilities.strategy_logic import get_daily_levels, calculate_jaeger_signals, add_sma_to_htf
from utilities.state_manager import StateManager

def load_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    try:
        with open(config_path, 'r') as f: return json.load(f)
    except Exception as e:
        logging.critical(f"Kritischer Fehler: Lade config.json: {e}"); sys.exit(1)
params = load_config()
BASE_DIR = os.path.expanduser(os.path.join("~", "jaegerbot"))
KEY_PATH = os.path.join(BASE_DIR, 'secret.json')
DB_PATH = os.path.join(os.path.dirname(__file__), f"tracker_{params['symbol'].replace('/', '-').replace(':', '-')}.db")
LOG_DIR = os.path.join(BASE_DIR, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'envelope.log')
logging.basicConfig(level=logging.INFO, format='%(asctime)s UTC: %(message)s', datefmt='%Y-%m-%d %H:%M:%S', handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
logger = logging.getLogger('jaeger_bot')
telegram_bot_token, telegram_chat_id = None, None
def send_telegram_message(message):
    if not telegram_bot_token or not telegram_chat_id: return
    url = f"https://api.telegram.org/bot{telegram_bot_token}/sendMessage"
    payload = {'chat_id': telegram_chat_id, 'text': message, 'parse_mode': 'Markdown'}
    try: requests.post(url, data=payload, timeout=10).raise_for_status()
    except requests.exceptions.RequestException as e: logger.error(f"Fehler bei Telegram: {e}")
logger.info(f">>> Starte Ausführung für {params['symbol']} (Jäger-Strategie)")
try:
    with open(KEY_PATH, "r") as f: secrets = json.load(f)
    api_setup = secrets['envelope']
    telegram_setup = secrets.get('telegram', {})
    telegram_bot_token, telegram_chat_id = telegram_setup.get('bot_token'), telegram_setup.get('chat_id')
except Exception as e:
    logger.critical(f"Kritischer Fehler beim Laden der Keys: {e}"); sys.exit(1)
state_manager = StateManager(DB_PATH)
def create_bitget_connection():
    for attempt in range(params['max_retries']):
        try: return BitgetFutures(api_setup)
        except Exception as e:
            logger.error(f"Verbindungsfehler (Versuch {attempt+1}/{params['max_retries']}): {e}")
            if attempt < params['max_retries'] - 1: time.sleep(params['retry_delay'])
    logger.critical("API-Verbindung fehlgeschlagen"); send_telegram_message(f"❌ *Kritischer Fehler:* API-Verbindung fehlgeschlagen."); sys.exit(1)
bitget = create_bitget_connection()
def get_trade_size_usdt():
    balance = bitget.fetch_balance().get('USDT', {}).get('total', 0.0)
    return (balance * (params['trade_size_pct'] / 100)) * params['leverage']
def cancel_all_trigger_orders(symbol):
    try:
        for order in bitget.fetch_open_trigger_orders(symbol):
            bitget.cancel_trigger_order(order['id'], symbol); logger.info(f"Bestehende Trigger-Order {order['id']} storniert.")
    except Exception as e: logger.error(f"Fehler beim Stornieren alter Trigger-Orders: {e}")
def open_position(side, ltf_data, daily_levels):
    try:
        logger.info(f"Öffne eine neue {side} Position basierend auf dem Retest.")
        trade_size_usdt = get_trade_size_usdt()
        if trade_size_usdt < 5.0:
            logger.error(f"Handelsgröße ({trade_size_usdt:.2f} USDT) zu gering.")
            return
        current_price = ltf_data.iloc[-1]['close']
        amount = trade_size_usdt / current_price
        cancel_all_trigger_orders(params['symbol'])
        bitget.place_market_order(params['symbol'], 'buy' if side == 'long' else 'sell', amount)
        logger.info(f"{side.capitalize()}-Position @ {current_price:.4f} eröffnet.")
        sl_price = 0
        sl_placement_pct = params.get('initial_sl_placement_pct', 0.1)
        if params['enable_initial_stop_loss']:
            if side == 'long':
                sl_price = daily_levels['body_top'] * (1 - sl_placement_pct / 100)
            else:
                sl_price = daily_levels['body_bottom'] * (1 + sl_placement_pct / 100)
            sl_side = 'sell' if side == 'long' else 'buy'
            sl_order = bitget.place_trigger_market_order(params['symbol'], sl_side, amount, sl_price, reduce=True)
            state_manager.set_state(status="in_trade_part_1", last_side=side, stop_loss_ids=[sl_order['id']])
            msg = f"✅ *Neue Position ({params['symbol']})*\nSeite: *{side.upper()}* @ {current_price:.4f}\nZiel 1 (TP1): {daily_levels[f'wick_{"high" if side == "long" else "low"}']:.4f}\nInitialer SL: {sl_price:.4f}"
            logger.info(msg.replace('*', '')); send_telegram_message(msg)
    except Exception as e:
        logger.error(f"Fehler beim Eröffnen der Position: {e}"); send_telegram_message(f"❌ Fehler bei Positionseröffnung: {e}"); state_manager.set_state(status="ok_to_trade")
def manage_open_position(position, ltf_data, daily_levels):
    state = state_manager.get_state()
    side = position['side']
    entry_price = float(position['entryPrice'])
    if state['status'] == 'in_trade_part_1':
        logger.info(f"Position Phase 1 ({side}) wird überwacht. PnL: {position.get('unrealizedPnl', 0):.2f} USDT")
        current_price = ltf_data.iloc[-1]['close']
        tp1_price = daily_levels['wick_high'] if side == 'long' else daily_levels['wick_low']
        is_tp1_hit = (side == 'long' and current_price >= tp1_price) or (side == 'short' and current_price <= tp1_price)
        if is_tp1_hit:
            logger.info(f"Ziel 1 (TP1) bei {tp1_price:.4f} erreicht!")
            try:
                total_amount = float(position['contracts'])
                close_amount = total_amount * params['trade_split_ratio']
                close_side = 'sell' if side == 'long' else 'buy'
                bitget.place_market_order(params['symbol'], close_side, close_amount, reduce=True)
                for sl_id in state.get('stop_loss_ids', []): bitget.cancel_trigger_order(sl_id, params['symbol'])
                new_sl_amount = total_amount - close_amount
                new_sl_order = bitget.place_trigger_market_order(params['symbol'], close_side, new_sl_amount, entry_price, reduce=True)
                state_manager.set_state(status="in_trade_part_2", last_side=side, stop_loss_ids=[new_sl_order['id']])
                msg = f"💰 *TP1 erreicht ({params['symbol']})*\nGewinn für 50% gesichert.\nSL auf Break-Even ({entry_price:.4f}) gesetzt."
                logger.info(msg.replace('*','')); send_telegram_message(msg)
            except Exception as e:
                logger.error(f"Fehler bei TP1: {e}. Schließe Position."); send_telegram_message(f"⚠️ Kritischer Fehler bei TP1. Schließe Position.")
                bitget.flash_close_position(params['symbol']); state_manager.set_state(status="ok_to_trade")
    elif state['status'] == 'in_trade_part_2':
        logger.info(f"Position Phase 2 ({side}, Runner) wird überwacht. PnL: {position.get('unrealizedPnl', 0):.2f} USDT")
        if not params.get('enable_trailing_stop_loss', False): return
        try:
            sl_id = state['stop_loss_ids'][0]
            current_sl_order = next((o for o in bitget.fetch_open_trigger_orders(params['symbol']) if o['id'] == sl_id), None)
            if not current_sl_order:
                logger.warning(f"Trailing Stop: SL-Order {sl_id} nicht gefunden."); return
            current_sl_price = float(current_sl_order['stopPrice'])
            lookback = params.get('tsl_lookback_candles', 2)
            relevant_candles = ltf_data.iloc[-(lookback+1):-1] 
            new_tsl_price = relevant_candles['low'].min() if side == 'long' else relevant_candles['high'].max()
            should_trail = (side == 'long' and new_tsl_price > current_sl_price) or (side == 'short' and new_tsl_price < current_sl_price)
            if should_trail:
                logger.info(f"Trailing Stop Update: Verschiebe SL von {current_sl_price:.4f} nach {new_tsl_price:.4f}")
                bitget.cancel_trigger_order(sl_id, params['symbol'])
                amount = float(position['contracts'])
                sl_side = 'sell' if side == 'long' else 'buy'
                new_sl_order = bitget.place_trigger_market_order(params['symbol'], sl_side, amount, new_tsl_price, reduce=True)
                state_manager.set_state(status="in_trade_part_2", last_side=side, stop_loss_ids=[new_sl_order['id']])
                send_telegram_message(f"📈 *Trailing Stop Update ({params['symbol']}):* Neuer SL bei {new_tsl_price:.4f} USDT")
        except Exception as e: logger.error(f"Fehler beim Trailing Stop Management: {e}")

# =============================================================================
# KORRIGIERTE HAUPTFUNKTION (main)
# =============================================================================
def main():
    try:
        # 1. ZUERST Positionen abrufen, um den aktuellen Status zu kennen
        positions = bitget.fetch_open_positions(params['symbol'])
        is_position_open = len(positions) > 0
        state = state_manager.get_state()

        # 2. Hebel und Margin-Modus NUR setzen, wenn KEINE Position offen ist
        if not is_position_open:
            try:
                margin_mode = params.get('margin_mode', 'isolated')
                leverage = params.get('leverage', 10)
                logger.info(f"Keine Position offen. Synchronisiere Margin-Modus auf '{margin_mode}' und Hebel auf {leverage}x...")
                bitget.set_margin_mode(params['symbol'], margin_mode)
                bitget.set_leverage(params['symbol'], leverage, margin_mode)
                logger.info("Margin-Modus und Hebel erfolgreich synchronisiert.")
            except Exception as e:
                logger.error(f"FEHLER bei der Synchronisierung von Hebel/Margin-Modus: {e}")
                send_telegram_message(f"⚠️ *Warnung ({params['symbol']}):* Konnte Hebel/Margin-Modus nicht setzen: {e}")
        
        # 3. Daten abrufen und Signale berechnen
        htf_data = bitget.fetch_recent_ohlcv(params['symbol'], params['htf_timeframe'], 100)
        ltf_data = bitget.fetch_recent_ohlcv(params['symbol'], params['ltf_timeframe'], 200)
        daily_levels = get_daily_levels(htf_data)
        if not daily_levels:
            logger.warning("Konnte keine gültigen Tageslevel finden."); return
        
        logger.info(f"Tageskerze vom {daily_levels['timestamp'].strftime('%Y-%m-%d')}: Top={daily_levels['body_top']:.2f}, Bottom={daily_levels['body_bottom']:.2f}")
        filter_params = params.get('sma_filter', {}); use_filter = filter_params.get('enabled', False)
        ltf_data = calculate_jaeger_signals(ltf_data, daily_levels, params); last_candle = ltf_data.iloc[-2]
        use_longs, use_shorts = params['use_longs'], params['use_shorts']
        if use_filter:
            htf_data = add_sma_to_htf(htf_data, params)
            htf_trend = htf_data.iloc[-2]['sma_trend']
            sma_period = filter_params.get('period', 20)
            if htf_trend == 1:
                use_shorts = False; logger.info(f"SMA({sma_period}) Filter: HTF-Trend ist AUFWÄRTS. Nur Longs.")
            elif htf_trend == -1:
                use_longs = False; logger.info(f"SMA({sma_period}) Filter: HTF-Trend ist ABWÄRTS. Nur Shorts.")
        
        # 4. Bot-Status mit Börsen-Status synchronisieren
        if state['status'] != "ok_to_trade" and not is_position_open:
            logger.warning("Tracker war 'in_trade', aber keine Position gefunden. Setze zurück.")
            send_telegram_message(f"ℹ️ *Info ({params['symbol']}):* Position extern geschlossen. Setze Bot-Status zurück.")
            state_manager.set_state(status="ok_to_trade")

        # 5. Handelslogik ausführen
        if is_position_open:
            manage_open_position(positions[0], ltf_data, daily_levels)
        else:
            if state['status'] != "ok_to_trade": state_manager.set_state(status="ok_to_trade")
            if last_candle['buy_signal'] and use_longs:
                logger.info("Long-Einstiegssignal (Retest) erkannt."); open_position('long', ltf_data, daily_levels)
            elif last_candle['sell_signal'] and use_shorts:
                logger.info("Short-Einstiegssignal (Retest) erkannt."); open_position('short', ltf_data, daily_levels)
            else:
                logger.info("Warte auf Retest (oder Signal wurde durch Filter blockiert).")

    except Exception as e:
        logger.error(f"Unerwarteter Fehler in der Hauptschleife: {e}")
        send_telegram_message(f"❌ *Unerwarteter Fehler ({params['symbol']}):* {e}")


if __name__ == "__main__":
    main()
    logger.info(f"<<< Ausführung abgeschlossen\n")
