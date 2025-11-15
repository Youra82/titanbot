# test_api.py
import os
import sys
import json
import logging
import time
from unittest.mock import patch

# Pfad-Setup
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
# Füge den src-Ordner zum Pfad hinzu, wie es die Skripte erwarten
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

# Importiere die Funktionen direkt über die Paketstruktur
from titanbot.utils.exchange import Exchange
from titanbot.utils.trade_manager import check_and_open_new_position, housekeeper_routine, is_trade_locked, set_trade_lock
from titanbot.utils.timeframe_utils import determine_htf
from titanbot.strategy.smc_engine import Bias
# Nur importieren, um sicherzustellen, dass die Abhängigkeit geladen wird
from titanbot.strategy.trade_logic import get_titan_signal 


# Logging Setup (minimal)
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(name)s: %(message)s')
logger = logging.getLogger("test_api_runner")

def run_test():
    try:
        # 1. Konfiguration laden
        with open(os.path.join(PROJECT_ROOT, 'secret.json'), "r") as f:
            secrets = json.load(f)
        
        test_account = secrets['titanbot'][0]
        telegram_config = secrets.get('telegram', {})
        exchange = Exchange(test_account)

        # 2. Mock-Parameter (wie im echten Test)
        symbol = 'XRP/USDT:USDT'
        timeframe = '5m'
        htf = determine_htf(timeframe)
        
        params = {
            'market': {'symbol': symbol, 'timeframe': timeframe, 'htf': htf}, 
            'strategy': { 'swingsLength': 20, 'ob_mitigation': 'High/Low' },
            'risk': {
                'margin_mode': 'isolated',
                'risk_per_trade_pct': 0.5,
                'risk_reward_ratio': 2.0,
                'leverage': 15,
                'trailing_stop_activation_rr': 1.5,
                'trailing_stop_callback_rate_pct': 0.5,
                'atr_multiplier_sl': 1.0,
                'min_sl_pct': 0.1
            },
            'behavior': { 'use_longs': True, 'use_shorts': True }
        }

        # 3. Führe die kritische Funktion aus
        logger.info(f"Führe API-Test für {symbol} aus...")
        
        # HOUSEKEEPER vor dem Test (damit die Order nicht fehlschlägt, falls offen)
        housekeeper_routine(exchange, symbol, logger)
        
        # WICHTIG: Korrekter Patch-Pfad, der direkt auf die Funktion im trade_manager zielt.
        # Dies ist der robusteste Weg, wenn der Aufrufer und die zu mockende Funktion
        # in verschiedenen Modulen liegen.
        with patch('titanbot.utils.trade_manager.get_titan_signal', return_value=('buy', None)):
            # Die Funktion benötigt Dummy-Model/Scaler für Kompatibilität mit dem Funktionsaufruf
            check_and_open_new_position(exchange, None, None, params, telegram_config, logger)

        logger.info("Test beendet. Prüfen Sie die Logs auf API-Fehler (Code 40774 oder InsufficientFunds).")

    except Exception as e:
        logger.error(f"Kritischer Fehler während der API-Ausführung: {e}")

if __name__ == "__main__":
    run_test()
