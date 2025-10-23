# /root/titanbot/tests/test_workflow.py
import pytest
import os
import sys
import json
import logging
import time
from unittest.mock import patch # Wichtig für das Mocking

# Füge das Projektverzeichnis zum Python-Pfad hinzu
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from titanbot.utils.exchange import Exchange
from titanbot.utils.trade_manager import check_and_open_new_position, housekeeper_routine

# --- Kein FakeModel/Scaler mehr benötigt ---

@pytest.fixture(scope="module") 
def test_setup():
    """
    Bereitet die Testumgebung vor und räumt danach auf.
    """
    print("\n--- Starte umfassenden LIVE TitanBot-Workflow-Test ---")
    print("\n[Setup] Bereite Testumgebung vor...")

    secret_path = os.path.join(PROJECT_ROOT, 'secret.json')
    if not os.path.exists(secret_path):
         pytest.skip("secret.json nicht gefunden. Überspringe Live-Workflow-Test.")

    with open(secret_path, 'r') as f:
        secrets = json.load(f)

    if not secrets.get('jaegerbot') or not secrets['jaegerbot']:
        pytest.skip("Es wird mindestens ein Account unter 'jaegerbot' in secret.json für den Workflow-Test benötigt.")

    test_account = secrets['jaegerbot'][0]
    telegram_config = secrets.get('telegram', {})

    try:
        exchange = Exchange(test_account)
        if not exchange.markets:
             pytest.fail("Exchange konnte nicht initialisiert werden (Märkte nicht geladen).")
    except Exception as e:
         pytest.fail(f"Exchange konnte nicht initialisiert werden: {e}")


    symbol = 'BTC/USDT:USDT' 
    params = {
        'market': {'symbol': symbol, 'timeframe': '5m'}, 
        'strategy': { 'swingsLength': 20, 'ob_mitigation': 'High/Low' },
        'risk': { 
            'margin_mode': 'isolated',
            'risk_per_trade_pct': 1.0,
            'risk_reward_ratio': 2.0,
            'leverage': 7, 
            'trailing_stop_activation_rr': 1.5,
            'trailing_stop_callback_rate_pct': 0.5
        },
        'behavior': { 'use_longs': True, 'use_shorts': True }
    }

    test_logger = logging.getLogger("test-logger")
    test_logger.setLevel(logging.INFO)
    if not test_logger.handlers:
        test_logger.addHandler(logging.StreamHandler(sys.stdout))

    print("-> Führe initiales Aufräumen durch...")
    try:
        housekeeper_routine(exchange, symbol, test_logger)
        time.sleep(2)
        pos_check = exchange.fetch_open_positions(symbol)
        if pos_check:
             print(f"WARNUNG: Position für {symbol} nach initialem Aufräumen noch vorhanden. Versuche erneut zu schließen...")
             exchange.create_market_order(symbol, 'sell' if pos_check[0]['side'] == 'long' else 'buy', float(pos_check[0]['contracts']), {'reduceOnly': True})
             time.sleep(3)
             pos_check_after = exchange.fetch_open_positions(symbol)
             if pos_check_after:
                  pytest.fail(f"Konnte initiale Position für {symbol} nicht schließen.")
             else:
                  print("-> Initiale Position erfolgreich geschlossen.")
                  housekeeper_routine(exchange, symbol, test_logger) 
                  time.sleep(1)

        print("-> Ausgangszustand ist sauber.")
    except Exception as e:
        pytest.fail(f"Fehler beim initialen Aufräumen: {e}")

    yield exchange, params, telegram_config, symbol, test_logger

    # --- Teardown ---
    print("\n[Teardown] Räume nach dem Test auf...")
    try:
        # 1. Alle Trigger Orders (SL/TP) löschen
        print("-> Lösche offene Trigger Orders...")
        exchange.cancel_all_orders_for_symbol(symbol) 
        time.sleep(2) 

        # 2. Prüfen, ob noch eine Position offen ist und diese schließen
        print("-> Prüfe auf offene Positionen...")
        position = exchange.fetch_open_positions(symbol)
        if position:
            print(f"-> Position nach Test noch offen ({position[0]['side']} {position[0]['contracts']}). Schließe sie notfallmäßig...")
            exchange.create_market_order(symbol, 'sell' if position[0]['side'] == 'long' else 'buy', float(position[0]['contracts']), {'reduceOnly': True})
            time.sleep(3) 
            print("-> Position sollte jetzt geschlossen sein.")
        else:
            print("-> Keine offene Position gefunden.")

        # 3. Nochmal alle Orders löschen (Sicherheitsnetz für verwaiste TP/SL)
        print("-> Führe finale Order-Löschung durch...")
        exchange.cancel_all_orders_for_symbol(symbol)
        print("-> Aufräumen abgeschlossen.")

    except Exception as e:
        print(f"FEHLER beim Aufräumen nach dem Test: {e}")


def test_full_titanbot_workflow_on_bitget(test_setup):
    """
    Testet den Handelsablauf (Order-Eröffnung, SL/TP-Platzierung) über den trade_manager
    auf dem konfigurierten Live-Konto mit einem gemockten SMC-Signal.
    """
    exchange, params, telegram_config, symbol, logger = test_setup

    print("\n[Schritt 1/3] Mocke Signal und prüfe Trade-Eröffnung über den Trade-Manager...")

    with patch('titanbot.utils.trade_manager.get_titan_signal', return_value=('buy', None)):
        check_and_open_new_position(exchange, None, None, params, telegram_config, logger)

    print("-> Warte 5s auf Order-Ausführung und Bestätigung...")
    time.sleep(5) 

    print("\n[Schritt 2/3] Überprüfe, ob die Position und SL/TP korrekt erstellt wurden...")
    position = exchange.fetch_open_positions(symbol)
    assert position, "FEHLER: Position wurde nicht eröffnet!"
    assert len(position) == 1, f"FEHLER: Unerwartete Anzahl offener Positionen ({len(position)} statt 1)."
    pos_info = position[0]
    assert pos_info.get('marginMode'), f"FEHLER: Margin-Modus nicht in Positionsdaten gefunden."
    print(f"-> ✔ Position korrekt eröffnet ({pos_info.get('marginMode', 'N/A')}, {pos_info.get('leverage', 'N/A')}x).")

    trigger_orders = exchange.fetch_open_trigger_orders(symbol) 
    
    # ***** KORRIGIERTE ASSERTION *****
    assert len(trigger_orders) == 2, f"FEHLER: Falsche Anzahl an SL/TP-Orders gefunden ({len(trigger_orders)} statt 2)."
    print("-> ✔ SL & TP Trigger-Orders erfolgreich platziert.")

    print("\n[Schritt 3/3] Test erfolgreich, Aufräumen wird im Teardown durchgeführt.")
    print("\n--- ✅ UMFASSENDER WORKFLOW-TEST ERFOLGREICH! ---")
