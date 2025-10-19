# src/titanbot/strategy/run.py
import os
import sys
import json
import logging
from logging.handlers import RotatingFileHandler
import time
import argparse
import ccxt

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from titanbot.utils.exchange import Exchange
from titanbot.utils.telegram import send_message
from titanbot.utils.trade_manager import full_trade_cycle
# *** Guardian Import entfernt ***
# from titanbot.utils.guardian import guardian_decorator # Entfernt

def setup_logging(symbol, timeframe):
    safe_filename = f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"
    log_dir = os.path.join(PROJECT_ROOT, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f'titanbot_{safe_filename}.log')

    logger = logging.getLogger(f'titanbot_{safe_filename}')
    logger.setLevel(logging.INFO)

    # Verhindere doppelte Handler, falls das Skript mehrmals im selben Prozess aufgerufen wird
    if not logger.handlers:
        # File Handler
        fh = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
        fh_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        fh.setFormatter(fh_formatter)
        logger.addHandler(fh)

        # Console Handler
        ch = logging.StreamHandler()
        # Angepasstes Format für Konsole für bessere Lesbarkeit
        ch_formatter = logging.Formatter(f'%(asctime)s [%(levelname)s] {symbol}|{timeframe}: %(message)s', datefmt='%H:%M:%S')
        ch.setFormatter(ch_formatter)
        logger.addHandler(ch)
        
        # Wichtig: Verhindere, dass Logs an den Root-Logger weitergegeben werden
        logger.propagate = False

    return logger


def load_config(symbol, timeframe, use_macd_filter):
    configs_dir = os.path.join(PROJECT_ROOT, 'src', 'titanbot', 'strategy', 'configs')
    safe_filename_base = f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"

    # Suffix-Logik beibehalten für Flexibilität, aber für SMC meist leer
    suffix = "_macd" if use_macd_filter else ""
    config_filename = f"config_{safe_filename_base}{suffix}.json"
    config_path = os.path.join(configs_dir, config_filename)

    if not os.path.exists(config_path):
        # Fallback ohne Suffix
        config_filename_fallback = f"config_{safe_filename_base}.json"
        config_path_fallback = os.path.join(configs_dir, config_filename_fallback)
        if os.path.exists(config_path_fallback):
            config_path = config_path_fallback
            config_filename = config_filename_fallback # Update filename for error message
        else:
             # Versuche Fallback mit _macd Suffix (falls use_macd false war, aber nur _macd existiert)
             config_filename_macd = f"config_{safe_filename_base}_macd.json"
             config_path_macd = os.path.join(configs_dir, config_filename_macd)
             if os.path.exists(config_path_macd):
                   config_path = config_path_macd
                   config_filename = config_filename_macd
             else:
                  raise FileNotFoundError(f"Konfigurationsdatei '{config_filename}' oder Fallbacks nicht gefunden.")

    with open(config_path, 'r') as f:
        return json.load(f)

# *** Guardian Decorator entfernt ***
# @guardian_decorator # Entfernt
def run_for_account(account, telegram_config, params, model, scaler, logger):
     """ Führt den Handelszyklus für einen Account aus. """
     # Füge einen try-except Block hier ein, um Fehler im Zyklus abzufangen,
     # da der Decorator weg ist.
     try:
         account_name = account.get('name', 'Standard-Account')
         symbol = params['market']['symbol']
         timeframe = params['market']['timeframe']

         logger.info(f"--- Starte TitanBot für {symbol} ({timeframe}) auf Account '{account_name}' ---")
         exchange = Exchange(account)
         
         # Prüfe ob Exchange Initialisierung erfolgreich war
         if not exchange.markets:
              logger.critical("Exchange konnte nicht initialisiert werden (Märkte nicht geladen). Breche Zyklus ab.")
              return

         # 'model' und 'scaler' werden als None übergeben und ignoriert
         full_trade_cycle(exchange, None, None, params, telegram_config, logger)

     except Exception as e:
          # Fange alle unerwarteten Fehler im Hauptzyklus ab
          symbol_f = params.get('market', {}).get('symbol', 'Unbekannt')
          tf_f = params.get('market', {}).get('timeframe', 'N/A')
          logger.critical(f"!!! KRITISCHER FEHLER im Hauptzyklus für {symbol_f} ({tf_f}) !!!")
          logger.critical(f"Fehlerdetails: {e}", exc_info=True) # Loggt den Traceback
          # Sende Telegram Nachricht bei kritischem Fehler
          try:
               error_message = f"🚨 *Kritischer Fehler* in TitanBot für *{symbol_f} ({tf_f})*:\n\n`{e}`\n\nBot-Instanz könnte instabil sein."
               send_message(
                    telegram_config.get('bot_token'),
                    telegram_config.get('chat_id'),
                    error_message
               )
          except Exception as tel_e:
               logger.error(f"Konnte keine Telegram-Fehlermeldung senden: {tel_e}")


def main():
    parser = argparse.ArgumentParser(description="TitanBot SMC Trading-Skript")
    parser.add_argument('--symbol', required=True, type=str)
    parser.add_argument('--timeframe', required=True, type=str)
    parser.add_argument('--use_macd', required=True, type=str) # Behalten als Dummy für master_runner
    args = parser.parse_args()

    symbol, timeframe = args.symbol, args.timeframe
    use_macd = args.use_macd.lower() == 'true' # Wird von load_config ggf. für Dateinamen genutzt

    logger = setup_logging(symbol, timeframe)

    try:
        params = load_config(symbol, timeframe, use_macd)
        MODEL, SCALER = None, None # Nicht benötigt für SMC

        with open(os.path.join(PROJECT_ROOT, 'secret.json'), "r") as f:
            secrets = json.load(f)
        
        # Lese Account-Konfigurationen (erwarte 'jaegerbot' Schlüssel)
        accounts_to_run = secrets.get('jaegerbot', []) 
        if not accounts_to_run:
             logger.critical("Keine Account-Konfigurationen unter 'jaegerbot' in secret.json gefunden!")
             sys.exit(1)
             
        telegram_config = secrets.get('telegram', {})
        
    except FileNotFoundError as e:
         logger.critical(f"Kritischer Initialisierungs-Fehler: Datei nicht gefunden - {e}", exc_info=True)
         sys.exit(1)
    except json.JSONDecodeError as e:
         logger.critical(f"Kritischer Initialisierungs-Fehler: JSON-Fehler in Konfigurationsdatei - {e}", exc_info=True)
         sys.exit(1)
    except Exception as e:
        logger.critical(f"Kritischer Initialisierungs-Fehler: {e}", exc_info=True)
        sys.exit(1)

    # Stelle sicher, dass accounts_to_run eine Liste ist
    if not isinstance(accounts_to_run, list):
        logger.critical("Fehler: 'jaegerbot'-Eintrag in secret.json ist keine Liste von Accounts.")
        sys.exit(1)

    # Führe für jeden Account den Handelszyklus aus
    for account in accounts_to_run:
         # Übergebe MODEL und SCALER als None
         run_for_account(account, telegram_config, params, None, None, logger)

    logger.info(f">>> TitanBot-Lauf für {symbol} ({timeframe}) abgeschlossen <<<\n")

if __name__ == "__main__":
    main()
