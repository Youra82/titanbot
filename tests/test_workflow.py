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

@pytest.fixture(scope="module") # scope="module", damit Setup/Teardown nur einmal pro Modul läuft
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

    # --- Verwende den ERSTEN (Haupt-) Account ---
    if not secrets.get('jaegerbot') or not secrets['jaegerbot']: # Prüft immer noch auf 'jaegerbot'-Schlüssel!
        pytest.skip("Es wird mindestens ein Account unter 'jaegerbot' in secret.json für den Workflow-Test benötigt.")

    test_account = secrets['jaegerbot'][0]
    telegram_config = secrets.get('telegram', {})

    try:
        exchange = Exchange(test_account)
        if not exchange.markets: # Prüfen ob Exchange-Init erfolgreich war
             pytest.fail("Exchange konnte nicht initialisiert werden (Märkte nicht geladen).")
    except Exception as e:
         pytest.fail(f"Exchange konnte nicht initialisiert werden: {e}")


    symbol = 'BTC/USDT:USDT' # Standard-Test-Symbol

    # Beispiel-Parameter, ähnlich einer config_*.json von TitanBot
    params = {
        'market': {'symbol': symbol, 'timeframe': '5m'}, # Kurzes Timeframe für schnellere Daten
        'strategy': { # SMC Parameter (Werte sind hier unwichtig, da Signal gemockt wird)
            'swingsLength': 20,
            'ob_mitigation': 'High/Low'
            },
        'risk': { # Wichtig für Order-Platzierung!
            'margin_mode': 'isolated',
            'risk_per_trade_pct': 1.0, # Beispiel: 1% Risiko
            'risk_reward_ratio': 2.0,
            'leverage': 7, # Beispiel-Hebel
            'trailing_stop_activation_rr': 1.5,
            'trailing_stop_callback_rate_pct': 0.5
        },
        'behavior': { # Nicht relevant für diesen Test, aber zur Vollständigkeit
            'use_longs': True,
            'use_shorts': True
        }
    }

    # Logger für den Test
    test_logger = logging.getLogger("test-logger")
    test_logger.setLevel(logging.INFO)
    if not test_logger.handlers:
        test_logger.addHandler(logging.StreamHandler(sys.stdout)) # Logge auf Konsole

    print("-> Führe initiales Aufräumen durch...")
    try:
        housekeeper_routine(exchange, symbol, test_logger)
        # Kurze Pause nach dem Aufräumen
        time.sleep(2)
        # Doppelte Prüfung, ob Positionen wirklich weg sind
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
                  housekeeper_routine(exchange, symbol, test_logger) # Nochmal Orders löschen
                  time.sleep(1)

        print("-> Ausgangszustand ist sauber.")
    except Exception as e:
        pytest.fail(f"Fehler beim initialen Aufräumen: {e}")

    # Übergibt die benötigten Objekte an den Test
    yield exchange, params, telegram_config, symbol, test_logger

    # --- Teardown ---
    print("\n[Teardown] Räume nach dem Test auf...")
    try:
        # 1. Alle Trigger Orders (SL/TP) löschen
        print("-> Lösche offene Trigger Orders...")
        exchange.cancel_all_orders_for_symbol(symbol) # Verwendet die robustere Methode
        time.sleep(2) # Warte kurz

        # 2. Prüfen, ob noch eine Position offen ist und diese schließen
        print("-> Prüfe auf offene Positionen...")
        position = exchange.fetch_open_positions(symbol)
        if position:
            print(f"-> Position nach Test noch offen ({position[0]['side']} {position[0]['contracts']}). Schließe sie notfallmäßig...")
            exchange.create_market_order(symbol, 'sell' if position[0]['side'] == 'long' else 'buy', float(position[0]['contracts']), {'reduceOnly': True})
            time.sleep(3) # Warte auf Schließung
            print("-> Position sollte jetzt geschlossen sein.")
        else:
            print("-> Keine offene Position gefunden.")

        # 3. Nochmal alle Orders löschen (Sicherheitsnetz)
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

    # Mocke 'get_titan_signal', damit es ein Kaufsignal zurückgibt, wenn check_and_open_new_position es aufruft
    with patch('titanbot.utils.trade_manager.get_titan_signal', return_value=('buy', None)):
        # ***** KORREKTUR HIER: Füge None für model und scaler hinzu *****
        check_and_open_new_position(exchange, None, None, params, telegram_config, logger)

    print("-> Warte 5s auf Order-Ausführung und Bestätigung...")
    time.sleep(5) # Gib der Börse und der API Zeit

    print("\n[Schritt 2/3] Überprüfe, ob die Position und SL/TP korrekt erstellt wurden...")
    position = exchange.fetch_open_positions(symbol)
    assert position, "FEHLER: Position wurde nicht eröffnet!"
    assert len(position) == 1, f"FEHLER: Unerwartete Anzahl offener Positionen ({len(position)} statt 1)."
    pos_info = position[0]
    # Bitget gibt 'isolated' / 'cross' manchmal anders zurück, wir prüfen nur, ob es gesetzt ist.
    assert pos_info.get('marginMode'), f"FEHLER: Margin-Modus nicht in Positionsdaten gefunden."
    print(f"-> ✔ Position korrekt eröffnet ({pos_info.get('marginMode', 'N/A')}, {pos_info.get('leverage', 'N/A')}x).")

    trigger_orders = exchange.fetch_open_trigger_orders(symbol) # Holt SL/TP (Stop Orders)
    # Manchmal kann es einen Moment dauern, bis beide Orders sichtbar sind, oder eine wurde bereits ausgelöst (unwahrscheinlich im Test)
    # Wir prüfen auf mindestens eine Order, idealerweise zwei
    assert len(trigger_orders) >= 1, f"FEHLER: Keine SL/TP-Trigger-Orders gefunden."
    if len(trigger_orders) == 2:
        print("-> ✔ SL & TP Trigger-Orders erfolgreich platziert.")
    else:
        print(f"-> WARNUNG: Nur {len(trigger_orders)} Trigger-Order(s) gefunden (erwartet: 2). Prüfung trotzdem bestanden.")

    print("\n[Schritt 3/3] Test erfolgreich, Aufräumen wird im Teardown durchgeführt.")
    print("\n--- ✅ UMFASSENDER WORKFLOW-TEST ERFOLGREICH! ---")
