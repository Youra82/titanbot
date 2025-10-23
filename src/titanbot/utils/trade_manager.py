# /root/titanbot/src/titanbot/utils/trade_manager.py
import logging
import time
import ccxt
import os
import json
from datetime import datetime
import pandas as pd # Für ATR Berechnung benötigt
import ta # Für ATR Berechnung benötigt
import math # Für math.ceil

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
LOCK_FILE_PATH = os.path.join(PROJECT_ROOT, 'artifacts', 'db', 'trade_lock.json')

from titanbot.utils.telegram import send_message
from titanbot.strategy.smc_engine import SMCEngine
from titanbot.strategy.trade_logic import get_titan_signal

# Logger für dieses Modul holen
logger = logging.getLogger(__name__)

def get_trade_lock(strategy_id):
    if not os.path.exists(LOCK_FILE_PATH): return None
    try:
        with open(LOCK_FILE_PATH, 'r') as f: locks = json.load(f)
        return locks.get(strategy_id)
    except (json.JSONDecodeError, FileNotFoundError): return None

def set_trade_lock(strategy_id, candle_timestamp):
    os.makedirs(os.path.dirname(LOCK_FILE_PATH), exist_ok=True)
    locks = {}
    if os.path.exists(LOCK_FILE_PATH):
        try:
            with open(LOCK_FILE_PATH, 'r') as f: locks = json.load(f)
        except json.JSONDecodeError: locks = {}
    locks[strategy_id] = candle_timestamp.strftime('%Y-%m-%d %H:%M:%S')
    with open(LOCK_FILE_PATH, 'w') as f: json.dump(locks, f, indent=4)

def housekeeper_routine(exchange, symbol, logger):
    logger.info(f"Starte Aufräum-Routine für {symbol}...")
    try:
        # Nutze die robustere cancel_all_orders_for_symbol
        cancelled_flag = exchange.cancel_all_orders_for_symbol(symbol)
        if cancelled_flag > 0: # Gibt 1 bei Erfolg zurück
            logger.info(f"Befehl zum Stornieren aller Orders für {symbol} gesendet.")
        else:
            logger.info("Keine offenen Orders zum Aufräumen gefunden oder Befehl fehlgeschlagen.")
    except Exception as e:
        logger.error(f"Fehler während der Aufräum-Routine: {e}", exc_info=True)


def check_and_open_new_position(exchange, model, scaler, params, telegram_config, logger):
    """
    Prüft auf neue SMC-Signale und eröffnet eine Position,
    jetzt mit ATR-basiertem Stop-Loss und JaegerBot Order-Logik.
    """
    symbol = params['market']['symbol']
    timeframe = params['market']['timeframe']
    strategy_id = f"{symbol}_{timeframe}"
    account_name = exchange.account.get('name', 'Standard-Account')

    logger.info("Suche nach neuen SMC-Signalen...")
    swing_length = params.get('strategy', {}).get('swingsLength', 50)
    # Lade mehr Daten für ATR(14) + SMC Engine
    limit_needed = max(swing_length * 3, 100) # Mindestens 100 Kerzen
    data = exchange.fetch_recent_ohlcv(symbol, timeframe, limit=limit_needed)

    if len(data) < 15: # Mindestanzahl für ATR(14)
        logger.warning(f"Nicht genügend Daten ({len(data)}) für ATR-Berechnung geladen. Überspringe.")
        return

    # --- ATR Berechnen ---
    try:
        atr_indicator = ta.volatility.AverageTrueRange(high=data['high'], low=data['low'], close=data['close'], window=14)
        data['atr'] = atr_indicator.average_true_range()
    except Exception as e:
         logger.error(f"Fehler bei ATR-Berechnung im Live-Modus: {e}. Überspringe Signalprüfung.")
         return

    if len(data) < 2:
         logger.warning("Zu wenige Datenpunkte nach ATR Berechnung.")
         return

    last_complete_candle = data.iloc[-2]
    current_candle = data.iloc[-1]
    last_candle_timestamp = last_complete_candle.name 

    # --- Lock-Datei prüfen ---
    last_trade_timestamp_str = get_trade_lock(strategy_id)
    if last_trade_timestamp_str and last_trade_timestamp_str == last_candle_timestamp.strftime('%Y-%m-%d %H:%M:%S'):
        logger.info(f"Signal für Kerze {last_candle_timestamp} wurde bereits gehandelt. Überspringe.")
        return

    # --- SMC Analyse (bis zur letzten abgeschlossenen Kerze) ---
    logger.info(f"Starte SMCEngine-Analyse für {len(data)-1} Kerzen...")
    smc_params = params.get('strategy', {})
    engine = SMCEngine(settings=smc_params)
    smc_results = engine.process_dataframe(data[['open','high','low','close']].iloc[:-1].copy())

    # --- Signal Logik ---
    side, _ = get_titan_signal(smc_results, current_candle, params)

    if side:
        logger.info(f"Gültiges SMC-Signal '{side.upper()}' für Kerze {last_candle_timestamp} erkannt. Beginne Trade-Eröffnung.")
        
        p = params['risk']
        current_balance = exchange.fetch_balance_usdt()
        if current_balance <= 0:
            logger.error("Kein Guthaben zum Eröffnen der Position vorhanden."); return

        logger.info(f"Verwende aktuellen Gesamt-Kontostand von {current_balance:.2f} USDT.")
        risk_amount_usd = current_balance * (p['risk_per_trade_pct'] / 100)

        ticker = exchange.fetch_ticker(symbol)
        if not ticker or 'last' not in ticker:
             logger.error("Konnte aktuellen Preis (Ticker) nicht abrufen. Breche Trade ab.")
             return
        market_entry_price = ticker['last']
        if market_entry_price <= 0:
             logger.error(f"Ungültiger Entry-Preis ({market_entry_price}) erhalten. Breche Trade ab.")
             return

        # --- ATR-basierter Stop-Loss ---
        current_atr = last_complete_candle.get('atr')
        if pd.isna(current_atr) or current_atr <= 0:
            logger.error(f"Ungültiger ATR-Wert ({current_atr}) für SL-Berechnung. Breche Trade ab.")
            return

        atr_multiplier_sl = 2.0 # Standard, kann aus Config kommen
        sl_distance = current_atr * atr_multiplier_sl
        min_sl_pct = 0.005 # 0.5% min SL (aus Backtester)
        sl_distance_min = market_entry_price * min_sl_pct
        sl_distance = max(sl_distance, sl_distance_min) # Nimm den größeren von ATR oder Min-%

        if sl_distance <= 0:
            logger.error(f"Stop-Loss-Distanz ist Null oder negativ ({sl_distance}). Breche Trade ab."); return

        sl_distance_pct_equivalent = sl_distance / market_entry_price
        if sl_distance_pct_equivalent <= 1e-6: # Div by zero check
             logger.error(f"Prozentualer SL-Abstand ist Null. Breche Trade ab."); return
        
        notional_value = risk_amount_usd / sl_distance_pct_equivalent
        amount = notional_value / market_entry_price
        
        # Positionsgrößen-Limits (aus Backtester)
        max_allowed_effective_leverage = 10 
        absolute_max_notional_value = 1000000
        min_notional = 5.0
        
        max_notional_by_leverage = current_balance * max_allowed_effective_leverage
        notional_value = min(notional_value, max_notional_by_leverage, absolute_max_notional_value)
        
        if notional_value < min_notional:
            logger.warning(f"Berechnete Positionsgröße ({notional_value:.2f} USDT) unter Minimum ({min_notional:.2f} USDT). Überspringe Trade.")
            set_trade_lock(strategy_id, last_candle_timestamp) # Sperre trotzdem setzen, um Spam zu vermeiden
            return
        
        # Menge (Amount) basierend auf finalem Notional Value neu berechnen
        amount = notional_value / market_entry_price
        
        stop_loss_price = market_entry_price - sl_distance if side == 'buy' else market_entry_price + sl_distance
        take_profit_price = market_entry_price + sl_distance * p['risk_reward_ratio'] if side == 'buy' else market_entry_price - sl_distance * p['risk_reward_ratio']

        try:
            if not exchange.set_leverage(symbol, p['leverage']):
                logger.warning(f"Konnte Hebel nicht setzen für {symbol}.")
            if not exchange.set_margin_mode(symbol, p['margin_mode']):
                logger.warning(f"Konnte Margin Mode nicht setzen für {symbol}.")

            logger.info(f"Platziere Market Order: {side.upper()} {amount:.6f} {symbol.split('/')[0]} @ Market (≈${market_entry_price:.4f})")
            order_params = {'marginMode': p['margin_mode']}
            market_order = exchange.create_market_order(symbol, side, amount, params=order_params)
            if not market_order: raise Exception("Market Order fehlgeschlagen (Antwort war None).")

            logger.info("Market Order platziert. Warte 2s auf Füllung/Bestätigung...")
            time.sleep(2)

            final_position = None
            for attempt in range(3):
                position_list = exchange.fetch_open_positions(symbol)
                if position_list:
                    final_position = position_list[0]; break
                logger.warning(f"Versuch {attempt+1}/3: Position noch nicht bestätigt, warte 2s...")
                time.sleep(2)

            if not final_position:
                logger.error("Position konnte nach Market Order nicht bestätigt werden! Aufräumen...")
                housekeeper_routine(exchange, symbol, logger)
                raise Exception("Positionsbestätigung fehlgeschlagen.")

            final_amount = float(final_position['contracts'])
            actual_entry_price = float(final_position.get('entryPrice', market_entry_price))
            logger.info(f"Position bestätigt: {final_position['side']} {final_amount:.6f} @ ${actual_entry_price:.4f}")

            # Neuberechnung SL/TP basierend auf tatsächlichem Entry Preis
            sl_distance = current_atr * atr_multiplier_sl # ATR bleibt gleich
            sl_distance_min = actual_entry_price * min_sl_pct
            sl_distance = max(sl_distance, sl_distance_min) # Erneut prüfen
            
            stop_loss_price = actual_entry_price - sl_distance if side == 'buy' else actual_entry_price + sl_distance
            take_profit_price = actual_entry_price + sl_distance * p['risk_reward_ratio'] if side == 'buy' else actual_entry_price - sl_distance * p['risk_reward_ratio']

            sl_rounded = float(exchange.exchange.price_to_precision(symbol, stop_loss_price))
            tp_rounded = float(exchange.exchange.price_to_precision(symbol, take_profit_price))
            
            logger.info(f"Platziere TP @ ${tp_rounded:.4f} und TSL (basierend auf Config) für Amount {final_amount:.6f}")

            # --- JAEGERBOT ORDER-LOGIK START ---

            # 1. Platziere Take Profit (als normale Trigger-Order)
            tp_order = exchange.place_trigger_market_order(symbol, 'sell' if side == 'buy' else 'buy', final_amount, tp_rounded, {'reduceOnly': True})

            # 2. Hole TSL-Parameter aus Config
            activation_rr = p.get('trailing_stop_activation_rr', 1.5)
            callback_rate_decimal = p.get('trailing_stop_callback_rate_pct', 0.5) / 100.0 # Umwandlung in Dezimal
            
            # Berechne Aktivierungspreis
            activation_price = actual_entry_price + sl_distance * activation_rr if side == 'buy' else actual_entry_price - sl_distance * activation_rr
            activation_price_rounded = float(exchange.exchange.price_to_precision(symbol, activation_price))

            tsl_order = None
            try:
                logger.info(f"Platziere Trailing-Stop: Aktivierung @ {activation_price_rounded}, Callback @ {callback_rate_decimal*100:.2f}%")
                tsl_order = exchange.place_trailing_stop_order(
                    symbol,
                    'sell' if side == 'buy' else 'buy',
                    final_amount,
                    activation_price_rounded, # Wann der TSL aktiviert wird
                    callback_rate_decimal,    # Wie weit er trailed (als Dezimal)
                    {'reduceOnly': True}
                )
            except Exception as tsl_e:
                logger.error(f"FEHLER: Platzierung des Trailing-Stop fehlgeschlagen: {tsl_e}. Platziere stattdessen fixen SL.")
                # Fallback auf fixen SL, falls TSL fehlschlägt
                tsl_order = exchange.place_trigger_market_order(symbol, 'sell' if side == 'buy' else 'buy', final_amount, sl_rounded, {'reduceOnly': True})
            
            # 3. Prüfen, ob BEIDE Orders platziert wurden
            if not tp_order or not tsl_order:
                logger.error("Fehler beim Platzieren von TP und/oder SL! Versuche aufzuräumen...")
                housekeeper_routine(exchange, symbol, logger)
                raise Exception("SL/TP Platzierung fehlgeschlagen.")

            # --- JAEGERBOT ORDER-LOGIK ENDE ---

            # Sperre setzen, NACHDEM Orders platziert wurden
            set_trade_lock(strategy_id, last_candle_timestamp)

            message = (f"🤖 TITAN Signal für *{account_name}* ({symbol} {timeframe}, {side.upper()})\n"
                       f"- Entry @ Market (≈${actual_entry_price:.4f})\n"
                       f"- Size: {final_amount:.4f} ({notional_value:.2f} USDT)\n"
                       f"- TP: ${tp_rounded:.4f} (RR: {p['risk_reward_ratio']:.2f})\n"
                       f"- TSL: Aktivierung @ ${activation_price_rounded:.4f}, Callback: {callback_rate_decimal*100:.2f}%")
            send_message(telegram_config.get('bot_token'), telegram_config.get('chat_id'), message)
            logger.info(f"Trade-Eröffnungsprozess erfolgreich abgeschlossen.")

        except ccxt.InsufficientFunds as e:
             logger.error(f"Fehler 'InsufficientFunds' beim Trade: {e}")
             # Lock nicht entfernen, da Guthaben fehlt
        except ccxt.ExchangeError as e:
             logger.error(f"Börsen-Fehler beim Trade: {e}", exc_info=True)
             # Lock nicht entfernen, da Börsenproblem vorliegen könnte
        except Exception as e:
            logger.error(f"Allgemeiner Fehler beim Eröffnen des Trades: {e}", exc_info=True)
            # Lock hier ggf. entfernen? Risiko: Endlosschleife
            # clear_trade_lock(strategy_id) 
            housekeeper_routine(exchange, symbol, logger) # Aufräumen versuchen

def full_trade_cycle(exchange, model, scaler, params, telegram_config, logger):
    """Der Haupt-Handelszyklus für eine einzelne Strategie."""
    symbol = params['market']['symbol']
    try:
        position = exchange.fetch_open_positions(symbol)
        position = position[0] if position else None
        if position:
            logger.info(f"Offene Position für {symbol} gefunden: {position.get('side')} {position.get('contracts')} @ ${position.get('entryPrice')}. Management durch SL/TP.")
        else:
            housekeeper_routine(exchange, symbol, logger)
            check_and_open_new_position(exchange, model, scaler, params, telegram_config, logger) # model/scaler werden als None übergeben
    except ccxt.DDoSProtection as e:
         logger.warning(f"DDoS Protection / Rate Limit: {e}. Warte 10s...")
         time.sleep(10)
    except ccxt.RequestTimeout as e:
         logger.warning(f"Request Timeout: {e}. Netzwerkproblem? Warte 5s...")
         time.sleep(5)
    except ccxt.NetworkError as e:
         logger.warning(f"Network Error: {e}. Verbindungsproblem? Warte 10s...")
         time.sleep(10)
    except ccxt.AuthenticationError as e:
        logger.critical(f"AUTHENTIFIZIERUNGSFEHLER: {e}. API-Schlüssel ungültig? Bot wird für diesen Account gestoppt!", exc_info=True)
        # sys.exit(1) # Beendet nur diesen Worker-Prozess
    except Exception as e:
        logger.error(f"Unerwarteter Fehler im Haupt-Handelszyklus für {symbol}: {e}", exc_info=True)
        time.sleep(5)
