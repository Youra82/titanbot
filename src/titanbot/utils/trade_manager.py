# src/titanbot/utils/trade_manager.py
import logging
import time
import ccxt
import os
import json
from datetime import datetime

# Pfade für die Lock-Datei definieren
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
LOCK_FILE_PATH = os.path.join(PROJECT_ROOT, 'artifacts', 'db', 'trade_lock.json')

# *** Geänderte Importpfade ***
from titanbot.utils.telegram import send_message
from titanbot.strategy.smc_engine import SMCEngine
from titanbot.strategy.trade_logic import get_titan_signal


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

#
# ======================================================================
# *** HIER BEGINNT DIE AUSGETAUSCHTE KERNLOGIK ***
# ======================================================================
#
def check_and_open_new_position(exchange, model, scaler, params, telegram_config, logger):
    """
    Prüft auf neue SMC-Handelssignale und eröffnet eine Position.
    'model' und 'scaler' werden aus der run.py-Datei noch übergeben, 
    aber für die SMC-Logik nicht mehr benötigt (wir behalten sie 
    der Einfachheit halber in der Signatur).
    """
    symbol = params['market']['symbol']
    timeframe = params['market']['timeframe']
    strategy_id = f"{symbol}_{timeframe}"
    account_name = exchange.account.get('name', 'Standard-Account')

    logger.info("Suche nach neuen SMC-Signalen...")
    # Lade genug Daten für die SMC-Analyse (z.B. 2x die Swing-Länge)
    swing_length = params.get('strategy', {}).get('swingsLength', 50)
    data = exchange.fetch_recent_ohlcv(symbol, timeframe, limit=swing_length * 3) 
    
    if len(data) < swing_length:
        logger.warning("Nicht genügend Daten für SMC-Analyse geladen. Überspringe.")
        return

    # Die letzte abgeschlossene Kerze ist die vorletzte
    last_candle_timestamp = data.index[-2]
    current_candle = data.iloc[-1] # Die (noch offene) aktuelle Kerze für die Preisprüfung

    # --- "EIN SCHUSS PRO KERZE" SICHERHEITSSPERRE ---
    last_trade_timestamp_str = get_trade_lock(strategy_id)
    if last_trade_timestamp_str and last_trade_timestamp_str == last_candle_timestamp.strftime('%Y-%m-%d %H:%M:%S'):
        logger.info(f"Signal für Kerze {last_candle_timestamp} wurde bereits gehandelt. Überspringe.")
        return

    # --- NEUE TITANBOT-LOGIK ---
    logger.info(f"Starte SMCEngine-Analyse für {len(data)} Kerzen...")
    
    # 1. Initialisiere die Engine mit den Params aus der Config
    smc_params = params.get('strategy', {})
    engine = SMCEngine(settings=smc_params)
    
    # 2. Verarbeite die gesamten historischen Daten
    # WICHTIG: Wir analysieren BIS ZUR LETZTEN ABGESCHLOSSENEN KERZE
    smc_results = engine.process_dataframe(data.iloc[:-1])
    
    # 3. Rufe die (separate) Handelslogik auf
    # Wir übergeben die SMC-Analyse, die aktuelle Kerze (für Preis-Checks) und die Params
    side, entry_price = get_titan_signal(smc_results, current_candle, params)
    # --- ENDE NEUE LOGIK ---

    if side:
        logger.info(f"Gültiges SMC-Signal '{side.upper()}' erkannt. Beginne Trade-Eröffnung.")
        
        # Setze die Sperre SOFORT, um doppelte Ausführung zu verhindern
        # Wir verwenden die Zeit der *analysierten* Kerze (die vorletzte)
        set_trade_lock(strategy_id, last_candle_timestamp)

        p = params['risk'] # Die Risiko-Sektion bleibt gleich

        current_balance = exchange.fetch_balance_usdt()
        if current_balance <= 0:
            logger.error("Kein Guthaben zum Eröffnen der Position vorhanden."); return

        logger.info(f"Verwende aktuellen Gesamt-Kontostand von {current_balance:.2f} USDT.")
        risk_amount_usd = current_balance * (p['risk_per_trade_pct'] / 100)

        # Verwende den aktuellen Marktpreis, 'entry_price' von get_titan_signal war nur zur Info
        ticker = exchange.fetch_ticker(symbol)
        market_entry_price = ticker['last']

        # --- Diese Risiko- & SL-Logik ist von JaegerBot, passe sie bei Bedarf an ---
        # TODO: Du solltest diese Logik anpassen, um den SL
        # basierend auf den SMC-Levels (z.B. FVG/OB-Grenze) zu setzen.
        sl_distance_pct = 0.015 # Beispiel: Fester SL. Besser wäre es, den SL von SMC abzuleiten
        sl_distance = market_entry_price * sl_distance_pct
        
        if sl_distance == 0:
            logger.error("Stop-Loss-Distanz ist Null. Trade kann nicht eröffnet werden."); return

        notional_value = risk_amount_usd / sl_distance_pct
        amount = notional_value / market_entry_price
        # --- Ende JaegerBot SL-Logik ---

        stop_loss_price = market_entry_price - sl_distance if side == 'buy' else market_entry_price + sl_distance
        take_profit_price = market_entry_price + sl_distance * p['risk_reward_ratio'] if side == 'buy' else market_entry_price - sl_distance * p['risk_reward_ratio']

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

            message = f"🤖 TITAN Signal für *{account_name}* ({symbol}, {side.upper()})\n- Entry @ Market (≈${market_entry_price:.4f})\n- SL: ${sl_rounded:.4f}\n- TP: ${tp_rounded:.4f}"
            send_message(telegram_config.get('bot_token'), telegram_config.get('chat_id'), message)
            logger.info(f"Trade-Eröffnungsprozess abgeschlossen.")
        
        except Exception as e:
            logger.error(f"Fehler beim Eröffnen des Trades: {e}", exc_info=True)
            # Lösche die Sperre, wenn der Trade fehlgeschlagen ist, um es erneut zu versuchen
            # (Optional, je nach gewünschtem Verhalten)
            # clear_trade_lock(strategy_id) 
            housekeeper_routine(exchange, symbol, logger)

#
# ======================================================================
# *** ENDE DES AUSGETAUSCHTEN CODES ***
# ======================================================================
#

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
