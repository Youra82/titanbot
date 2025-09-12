# code/strategies/envelope/run.py

import os
import sys
import json
import logging
import time
from utilities.bitget_futures import BitgetFutures

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..', '..', '..')
sys.path.append(os.path.join(PROJECT_ROOT, 'code'))

LOG_DIR = os.path.join(PROJECT_ROOT, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'livetradingbot.log')
logging.basicConfig(level=logging.INFO, format='%(asctime)s UTC %(levelname)s: %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S', handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
logger = logging.getLogger('titan_bot')

def load_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    with open(config_path, 'r') as f:
        return json.load(f)

params = load_config()
SYMBOL = params['market']['symbol']

def run_for_account(account):
    account_name = account.get("name", "Test-Account")
    bitget = BitgetFutures(account)

    logger.warning(f"[{account_name}] ACHTUNG: TEST-MODUS IST AKTIV!")

    # --- Margin Mode und Leverage setzen ---
    margin_mode = params['risk']['margin_mode']
    leverage = params['risk']['leverage']
    bitget.set_margin_mode(SYMBOL, margin_mode)
    bitget.set_leverage(SYMBOL, leverage)

    # --- Test-Trade setzen ---
    ticker = bitget.exchange.fetch_ticker(SYMBOL)
    price = ticker['last']

    balance = bitget.fetch_balance()
    usdt_available = balance.get('USDT', {}).get('free', 10)  # Test: max 10 USDT
    amount = usdt_available / price

    entry_price = price
    sl_price = entry_price * 0.99  # 1% unter Entry
    tp_price = entry_price * 1.03  # 3% über Entry
    side = 'buy'

    # Entry
    bitget.place_limit_order(SYMBOL, side, amount, entry_price)
    time.sleep(1)  # kurz warten

    # SL
    bitget.place_stop_loss_order(SYMBOL, side, amount, sl_price)
    time.sleep(1)

    # TP
    bitget.place_take_profit_order(SYMBOL, side, amount, tp_price)
    logger.info(f"[{account_name}] TEST-TRADE platziert: Entry={entry_price:.4f}, SL={sl_price:.4f}, TP={tp_price:.4f}")

def main():
    key_path = os.path.abspath(os.path.join(PROJECT_ROOT, 'secret.json'))
    with open(key_path, "r") as f:
        secrets = json.load(f)

    api_configs = secrets.get('titan')
    if isinstance(api_configs, dict):
        accounts = [api_configs]
    else:
        accounts = api_configs

    for account in accounts:
        run_for_account(account)

    logger.info(">>> TitanBot Testlauf abgeschlossen <<<")

if __name__ == "__main__":
    main()
