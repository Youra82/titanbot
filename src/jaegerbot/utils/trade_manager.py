# src/jaegerbot/utils/trade_manager.py
import logging
import time
import ccxt
import os
import json
from datetime import datetime

# Pfade für die Lock-Datei definieren
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
LOCK_FILE_PATH = os.path.join(PROJECT_ROOT, 'artifacts', 'db', 'trade_lock.json')

from jaegerbot.utils.telegram import send_message
from jaegerbot.utils.ann_model import create_ann_features

def get_trade_lock(strategy_id):
    """Liest den Zeitstempel des letzten Trades für eine Strategie aus der Lock-Datei."""
    if not os.path.exists(LOCK_FILE_PATH):
        return None
    try:
        with open(LOCK_FILE_PATH, 'r') as f:
            locks = json.load(f)
        return locks.get(strategy_id)
    except (json.JSONDecodeError, FileNotFoundError):
        return None

def set_trade_lock(strategy_id, candle_timestamp):
    """Setzt eine Sperre für eine Strategie, um erneutes Handeln auf derselben Kerze zu verhindern."""
    os.makedirs(os.path.dirname(LOCK_FILE_PATH), exist_ok=True)
    locks = {}
    if os.path.exists(LOCK_FILE_PATH):
        try:
            with open(LOCK_FILE_PATH, 'r') as f:
                locks = json.load(f)
        except json.JSONDecodeError:
            locks = {} # Überschreibe korrupte Datei
            
    locks[strategy_id] = candle_timestamp.strftime('%Y-%m-%d %H:%M:%S')
    with open(LOCK_FILE_PATH, 'w') as f:
        json.dump(locks, f, indent=4)

def housekeeper_routine(exchange, symbol, logger):
    """Storniert alle offenen Orders für ein Symbol, um einen sauberen Zustand sicherzustellen."""
    logger.info(f"Starte Aufräum-Routine für {symbol}...")
    try:
        cancelled_count = exchange.cleanup_all_open_orders(symbol)
        if cancelled_count > 0:
            logger.info(f"{cancelled_count} verwaiste Order(s) gefunden und storniert.")
        else:
            logger.info("Keine offenen Orders zum Aufräumen gefunden.")
    except Exception as e:
        logger.error(f"Fehler während der Aufräum-Routine: {e}", exc_info=True)

def check_and_open_new_position(exchange, model, scaler, params, telegram_config, logger):
    """
    Prüft auf neue Handelssignale und eröffnet eine Position, falls keine offen ist
    und für das Signal dieser Kerze noch kein Trade versucht wurde.
    """
    symbol = params['market']['symbol']
    timeframe = params['market']['timeframe']
    strategy_id = f"{symbol}_{timeframe}"
    account_name = exchange.account.get('name', 'Standard-Account')
    
    logger.info("Suche nach neuen Signalen...")
    data = exchange.fetch_recent_ohlcv(symbol, timeframe, limit=500) # Lade genug Daten für Indikatoren
    
    # Die letzte abgeschlossene Kerze ist die vorletzte in den heruntergeladenen Daten
    last_candle_timestamp = data.index[-2]
    
    # --- "EIN SCHUSS PRO KERZE" SICHERHEITSSPERRE ---
    last_trade_timestamp_str = get_trade_lock(strategy_id)
    if last_trade_timestamp_str and last_trade_timestamp_str == last_candle_timestamp.strftime('%Y-%m-%d %H:%M:%S'):
        logger.info(f"Signal für Kerze {last_candle_timestamp} wurde bereits gehandelt. Überspringe bis zur nächsten Kerze.")
        return
        
    data_with_features = create_ann_features(data.copy())
    
    feature_cols = ['bb_width', 'obv', 'rsi', 'macd_diff', 'hour', 'day_of_week', 'returns_lag1', 'returns_lag2', 'atr_normalized']
    latest_features = data_with_features.iloc[-2:-1][feature_cols] # Analysiere die letzte abgeschlossene Kerze

    if latest_features.isnull().values.any():
        logger.warning("Neueste Feature-Daten sind unvollständig, überspringe diesen Zyklus.")
        return

    prediction = model.predict(scaler.transform(latest_features), verbose=0)[0][0]
    logger.info(f"Analyse für Kerze {last_candle_timestamp} -> Modell-Vorhersage: {prediction:.3f}")
    
    pred_threshold = params['strategy']['prediction_threshold']
    side = None
    if prediction >= pred_threshold and params.get('behavior', {}).get('use_longs', True):
        side = 'buy'
    elif prediction <= (1 - pred_threshold) and params.get('behavior', {}).get('use_shorts', True):
        side = 'sell'
    
    behavior = params.get('behavior', {})
    if side and behavior.get('use_macd_trend_filter', False):
        filter_tf = behavior.get('macd_filter_timeframe', '1d')
        logger.info(f"Prüfe übergeordneten Trend mit MACD auf {filter_tf}...")
        try:
            htf_data = exchange.fetch_recent_ohlcv(symbol, filter_tf, limit=100)
            htf_data_features = create_ann_features(htf_data.copy())
            htf_macd_diff = htf_data_features.iloc[-2]['macd_diff']
            if side == 'buy' and htf_macd_diff < 0:
                logger.info(f"Long-Signal blockiert durch bärischen MACD auf {filter_tf}.")
                side = None
            elif side == 'sell' and htf_macd_diff > 0:
                logger.info(f"Short-Signal blockiert durch bullischen MACD auf {filter_tf}.")
                side = None
        except Exception as e:
            logger.error(f"Fehler im MACD-Filter. Handel wird übersprungen. Fehler: {e}"); side = None

    if side:
        logger.info(f"Gültiges Signal '{side.upper()}' für Kerze {last_candle_timestamp} erkannt. Beginne Trade-Eröffnung.")
        p = params['risk']
        
        current_balance = exchange.fetch_balance_usdt()
        if current_balance <= 0:
            logger.error("Kein Guthaben zum Eröffnen der Position vorhanden."); return
        
        logger.info(f"Verwende aktuellen Gesamt-Kontostand von {current_balance:.2f} USDT.")
        risk_amount_usd = current_balance * (p['risk_per_trade_pct'] / 100)
        
        ticker = exchange.fetch_ticker(symbol)
        entry_price = ticker['last']
        
        sl_distance_pct = 0.015
        sl_distance = entry_price * sl_distance_pct
        
        if sl_distance == 0:
            logger.error("Stop-Loss-Distanz ist Null. Trade kann nicht eröffnet werden."); return

        notional_value = risk_amount_usd / sl_distance_pct
        amount = notional_value / entry_price
        
        stop_loss_price = entry_price - sl_distance if side == 'buy' else entry_price + sl_distance
        take_profit_price = entry_price + sl_distance * p['risk_reward_ratio'] if side == 'buy' else entry_price - sl_distance * p['risk_reward_ratio']
        
        try:
            exchange.set_leverage(symbol, p['leverage'])
            
            order_params = {'marginMode': p['margin_mode']}
            exchange.create_market_order(symbol, side, amount, params=order_params)
            
            logger.info("Market Order platziert. Warte 2s zur Bestätigung...")
            time.sleep(2)
            
            final_position = exchange.fetch_open_positions(symbol)
            if not final_position: raise Exception("Position konnte nicht bestätigt werden.")
            final_amount = float(final_position[0]['contracts'])

            sl_rounded = float(exchange.exchange.price_to_precision(symbol, stop_loss_price))
            tp_rounded = float(exchange.exchange.price_to_precision(symbol, take_profit_price))

            exchange.place_trigger_market_order(symbol, 'sell' if side == 'buy' else 'buy', final_amount, tp_rounded, {'reduceOnly': True})
            exchange.place_trigger_market_order(symbol, 'sell' if side == 'buy' else 'buy', final_amount, sl_rounded, {'reduceOnly': True})
            
            # Setze die Sperre NACHDEM alle Orders erfolgreich platziert wurden
            set_trade_lock(strategy_id, last_candle_timestamp)
            
            message = f"🧠 ANN Signal für *{account_name}* ({symbol}, {side.upper()})\n- Entry @ Market (≈${entry_price:.4f})\n- SL: ${sl_rounded:.4f}\n- TP: ${tp_rounded:.4f}"
            send_message(telegram_config.get('bot_token'), telegram_config.get('chat_id'), message)
            logger.info(f"Trade-Eröffnungsprozess abgeschlossen und Signal-Sperre für Kerze {last_candle_timestamp} gesetzt.")
        except Exception as e:
            logger.error(f"Fehler beim Eröffnen des Trades: {e}", exc_info=True)
            housekeeper_routine(exchange, symbol, logger)

def full_trade_cycle(exchange, model, scaler, params, telegram_config, logger):
    """Der Haupt-Handelszyklus für eine einzelne Strategie."""
    symbol = params['market']['symbol']
    try:
        position = exchange.fetch_open_positions(symbol)
        position = position[0] if position else None
        if position:
            logger.info(f"Offene Position für {symbol} gefunden. Management wird übersprungen (nur SL/TP aktiv).")
        else:
            housekeeper_routine(exchange, symbol, logger)
            check_and_open_new_position(exchange, model, scaler, params, telegram_config, logger)
    except ccxt.InsufficientFunds as e:
        logger.error(f"Fehler: Nicht genügend Guthaben. {e}")
    except Exception as e:
        logger.error(f"Unerwarteter Fehler im Handelszyklus: {e}", exc_info=True)
