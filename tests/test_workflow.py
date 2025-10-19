# tests/test_workflow.py
import pytest
import os
import sys
import json
import logging
import time

# Füge das Projektverzeichnis zum Python-Pfad hinzu
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from jaegerbot.utils.exchange import Exchange
from jaegerbot.utils.trade_manager import check_and_open_new_position, housekeeper_routine

# Erstelle eine "Fake"-KI, die wir für den Test kontrollieren können
class FakeModel:
    """Eine Mock-Version des Keras-Modells, um das Vorhersage-Verhalten zu steuern."""
    def __init__(self):
        self.return_value = [[0.5]]
    def predict(self, data, verbose=0):
        return self.return_value

class FakeScaler:
    """Eine Mock-Version des Scalers, die einfach die Daten durchreicht."""
    def transform(self, data):
        return data

@pytest.fixture
def test_setup():
    """
    Bereitet die Testumgebung vor und räumt danach auf.
    """
    print("\n--- Starte umfassenden LIVE JaegerBot-Workflow-Test ---")
    print("\n[Setup] Bereite Testumgebung vor...")
    
    with open(os.path.join(PROJECT_ROOT, 'secret.json'), 'r') as f:
        secrets = json.load(f)
    
    # --- KORREKTUR: Verwende den ERSTEN (Haupt-) Account ---
    if not secrets.get('jaegerbot'):
        pytest.skip("Es wird mindestens ein Account in secret.json für den Workflow-Test benötigt.")
    
    test_account = secrets['jaegerbot'][0]
    telegram_config = secrets.get('telegram', {})
    
    exchange = Exchange(test_account)
    symbol = 'BTC/USDT:USDT' # Standard-Test-Symbol
    
    params = {
        'market': {'symbol': symbol, 'timeframe': '15m'},
        'strategy': {'prediction_threshold': 0.6},
        'behavior': {'use_longs': True, 'use_shorts': True, 'use_macd_trend_filter': False},
        'risk': {
            'risk_per_trade_pct': 1, 
            'risk_reward_ratio': 2.0, 
            'leverage': 7, 
            'margin_mode': 'isolated'
        }
    }
    
    model = FakeModel()
    scaler = FakeScaler()

    print("-> Führe initiales Aufräumen durch...")
    housekeeper_routine(exchange, symbol, logging.getLogger("test-logger"))
    print("-> Ausgangszustand ist sauber.")
    
    yield exchange, model, scaler, params, telegram_config, symbol
    
    print("\n[Teardown] Räume nach dem Test auf...")
    try:
        housekeeper_routine(exchange, symbol, logging.getLogger("test-logger"))
        position = exchange.fetch_open_positions(symbol)
        if position:
            print("Warnung: Position nach Test noch offen. Schließe sie notfallmäßig.")
            exchange.create_market_order(symbol, 'sell' if position[0]['side'] == 'long' else 'buy', float(position[0]['contracts']), {'reduceOnly': True})
    except Exception as e:
        print(f"Fehler beim Aufräumen: {e}")

def test_full_jaegerbot_workflow_on_bitget(test_setup):
    """
    Testet den gesamten Handelsablauf über den trade_manager auf dem konfigurierten Live-Konto.
    """
    exchange, model, scaler, params, telegram_config, symbol = test_setup
    logger = logging.getLogger("test-logger")
    
    model.return_value = [[0.9]] # Erzwingt ein "BUY"-Signal
    print("\n[Schritt 1/3] Prüfe Trade-Eröffnung über den Trade-Manager...")
    check_and_open_new_position(exchange, model, scaler, params, telegram_config, logger)
    time.sleep(5) 

    print("\n[Schritt 2/3] Überprüfe, ob die Position und SL/TP korrekt erstellt wurden...")
    position = exchange.fetch_open_positions(symbol)
    assert position, "FEHLER: Position wurde nicht eröffnet!"
    assert position[0]['marginMode'] == 'isolated', f"FEHLER: Position wurde im falschen Margin-Modus eröffnet: {position[0]['marginMode']}"
    print(f"-> ✔ Position korrekt eröffnet (Isolated, {position[0]['leverage']}x).")
    
    trigger_orders = exchange.fetch_open_trigger_orders(symbol)
    assert len(trigger_orders) == 2, f"FEHLER: Falsche Anzahl an SL/TP-Orders gefunden ({len(trigger_orders)} statt 2)."
    print("-> ✔ SL/TP erfolgreich platziert.")

    print("\n[Schritt 3/3] Test erfolgreich, Aufräumen wird im Teardown durchgeführt.")
    print("\n--- ✅ UMFASSENDER WORKFLOW-TEST ERFOLGREICH! ---")
