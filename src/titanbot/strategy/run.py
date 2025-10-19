# src/jaegerbot/strategy/run.py
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

from jaegerbot.utils.exchange import Exchange
from jaegerbot.utils.telegram import send_message
from jaegerbot.utils.ann_model import load_model_and_scaler
from jaegerbot.utils.trade_manager import full_trade_cycle
from jaegerbot.utils.guardian import guardian_decorator

def setup_logging(symbol, timeframe):
    safe_filename = f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"
    log_dir = os.path.join(PROJECT_ROOT, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f'jaegerbot_{safe_filename}.log')
    
    logger = logging.getLogger(f'jaegerbot_{safe_filename}')
    logger.setLevel(logging.INFO)
    
    if not logger.handlers:
        fh = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3)
        fh_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        fh.setFormatter(fh_formatter)
        logger.addHandler(fh)

        ch = logging.StreamHandler()
        ch_formatter = logging.Formatter('%(levelname)s: %(message)s')
        ch.setFormatter(ch_formatter)
        logger.addHandler(ch)
        
    return logger

def load_config(symbol, timeframe, use_macd_filter):
    configs_dir = os.path.join(PROJECT_ROOT, 'src', 'jaegerbot', 'strategy', 'configs')
    safe_filename_base = f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"
    
    suffix = "_macd" if use_macd_filter else ""
    config_filename = f"config_{safe_filename_base}{suffix}.json"
    config_path = os.path.join(configs_dir, config_filename)
    
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Die angeforderte Konfigurationsdatei '{config_filename}' wurde nicht gefunden.")
        
    with open(config_path, 'r') as f:
        return json.load(f)

@guardian_decorator
def run_for_account(account, telegram_config, params, model, scaler, logger):
    account_name = account.get('name', 'Standard-Account')
    symbol = params['market']['symbol']
    timeframe = params['market']['timeframe']
    
    logger.info(f"--- Starte JaegerBot für {symbol} ({timeframe}) auf Account '{account_name}' ---")
    exchange = Exchange(account)
    
    # Das Budget wird nicht mehr übergeben, da es dynamisch im trade_manager geholt wird
    full_trade_cycle(exchange, model, scaler, params, telegram_config, logger)

def main():
    parser = argparse.ArgumentParser(description="JaegerBot ANN Trading-Skript")
    parser.add_argument('--symbol', required=True, type=str)
    parser.add_argument('--timeframe', required=True, type=str)
    parser.add_argument('--use_macd', required=True, type=str)
    args = parser.parse_args()

    symbol, timeframe = args.symbol, args.timeframe
    use_macd = args.use_macd.lower() == 'true'
    
    logger = setup_logging(symbol, timeframe)

    try:
        params = load_config(symbol, timeframe, use_macd)
        safe_filename = f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"
        model_path = os.path.join(PROJECT_ROOT, 'artifacts', 'models', f'ann_predictor_{safe_filename}.h5')
        scaler_path = os.path.join(PROJECT_ROOT, 'artifacts', 'models', f'ann_scaler_{safe_filename}.joblib')
        MODEL, SCALER = load_model_and_scaler(model_path, scaler_path)
        
        if MODEL is None or SCALER is None:
            raise FileNotFoundError(f"Modell/Scaler für {symbol} ({timeframe}) nicht gefunden.")
            
        with open(os.path.join(PROJECT_ROOT, 'secret.json'), "r") as f:
            secrets = json.load(f)
        accounts_to_run = secrets.get('jaegerbot', [])
        telegram_config = secrets.get('telegram', {})
    except Exception as e:
        logger.critical(f"Kritischer Initialisierungs-Fehler: {e}", exc_info=True)
        sys.exit(1)
        
    for account in accounts_to_run:
        try:
            # Hier wird die dekorierte Funktion aufgerufen
            run_for_account(account, telegram_config, params, MODEL, SCALER, logger)
        except Exception as e:
            logger.error(f"Schwerwiegender Fehler bei Account {account.get('name', 'Unbenannt')}: {e}", exc_info=True)
    
    logger.info(f">>> JaegerBot-Lauf für {symbol} ({timeframe}) abgeschlossen <<<\n")

if __name__ == "__main__":
    main()
