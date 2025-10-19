# check_account_type.py
import os
import sys
import json
import ccxt
import pprint

# Pfad-Konfiguration, damit ccxt gefunden wird (Annahme: venv im Projektordner)
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
# Pfad ggf. an deine Python-Version anpassen
sys.path.append(os.path.join(PROJECT_ROOT, '.venv', 'lib', 'python3.12', 'site-packages')) 

print("--- Bitget Konto-Typ Diagnose ---")

SECRET_FILE = os.path.join(PROJECT_ROOT, 'secret.json')
if not os.path.exists(SECRET_FILE):
    print("Fehler: secret.json nicht gefunden.")
    sys.exit(1)

try:
    with open(SECRET_FILE, 'r') as f:
        secrets = json.load(f)
    
    # *** HINWEIS: Liest immer noch den Schlüssel 'jaegerbot'. ***
    # Du könntest diesen Schlüssel in secret.json in 'titanbot' umbenennen
    # und dann hier auch 'titanbot' verwenden für volle Konsistenz.
    # Aktuell funktioniert es aber auch so.
    if 'jaegerbot' not in secrets or not secrets['jaegerbot']:
         print("Fehler: Kein 'jaegerbot'-Eintrag in secret.json gefunden oder Liste ist leer.")
         sys.exit(1)
    account_config = secrets['jaegerbot'][0] 
    account_name = account_config.get('name', 'Unbenannt')
    print(f"Prüfe Account: {account_name}")

    print("Verbinde mit Bitget API...")
    exchange = ccxt.bitget({
        'apiKey': account_config.get('apiKey'),
        'secret': account_config.get('secret'),
        'password': account_config.get('password'),
        'options': {'defaultType': 'swap'},
    })

    print("Frage Kontoinformationen von Bitget ab...")
    balance_response = exchange.fetch_balance()

    # --- DIAGNOSE-LOGIK ---
    is_unified = False
    info = balance_response.get('info', {})
    if 'data' in info and isinstance(info['data'], list) and len(info['data']) > 0:
        first_item = info['data'][0]
        # Prüft auf Schlüssel, die typisch für Unified Accounts sind
        if 'accountType' in first_item or 'crossMarginWallet' in first_item or 'available' in first_item:
             # 'available' ist oft auch in Unified-Antworten vorhanden
             is_unified = True
             # Zusätzliche Prüfung, falls 'accountType' nicht direkt da ist
             if 'accountType' not in first_item and isinstance(first_item.get('assets'), list):
                  is_unified = True # Unified hat oft eine asset-Liste hier

    print("\n" + "="*30)
    print("      DIAGNOSE-ERGEBNIS")
    print("="*30)
    if is_unified:
        print("\n>>> KONTOTYP: Einheitliches Handelskonto (Unified Trading Account) <<<")
        # *** Text angepasst ***
        print("\nBEFUND: Dies kann zu Problemen führen, da TitanBot (wie JaegerBot) primär für das 'Klassische Konto' entwickelt wurde. Die API-Logik unterscheidet sich.")
    else:
        print("\n>>> KONTOTYP: Klassisches Konto (Classic Account) <<<")
        print("\nBEFUND: Das ist der erwartete Kontotyp. Probleme liegen wahrscheinlich woanders.")
    print("="*30)

    print("\n--- Rohdaten der API-Antwort ('info'-Sektion) ---")
    pprint.pprint(info)

except Exception as e:
    print(f"\nEin Fehler ist aufgetreten: {e}")
