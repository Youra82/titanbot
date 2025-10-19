# check_account_type.py
import os
import sys
import json
import ccxt
import pprint

# Pfad-Konfiguration, damit ccxt gefunden wird
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(PROJECT_ROOT, '.venv', 'lib', 'python3.12', 'site-packages'))

print("--- Bitget Konto-Typ Diagnose ---")

SECRET_FILE = os.path.join(PROJECT_ROOT, 'secret.json')
if not os.path.exists(SECRET_FILE):
    print("Fehler: secret.json nicht gefunden.")
    sys.exit(1)

try:
    with open(SECRET_FILE, 'r') as f:
        secrets = json.load(f)
    account_config = secrets.get('jaegerbot')[0]

    print("Verbinde mit Bitget API...")
    exchange = ccxt.bitget({
        'apiKey': account_config.get('apiKey'),
        'secret': account_config.get('secret'),
        'password': account_config.get('password'),
        'options': {'defaultType': 'swap'},
    })

    print("Frage Kontoinformationen von Bitget ab...")
    # fetch_balance() liefert je nach Kontotyp eine unterschiedliche Struktur
    balance_response = exchange.fetch_balance()
    
    # --- DIAGNOSE-LOGIK ---
    # Wir prüfen auf Merkmale, die typisch für ein Unified Account sind.
    # Die 'info'-Struktur ist hier der Schlüssel.
    is_unified = False
    info = balance_response.get('info', {})
    if 'data' in info and isinstance(info['data'], list) and len(info['data']) > 0:
        # Unified Accounts haben oft eine Liste von Wallets in 'data'
        # und einen 'accountType'-Schlüssel
        first_item = info['data'][0]
        if 'accountType' in first_item or 'crossMarginWallet' in first_item:
            is_unified = True

    print("\n" + "="*30)
    print("     DIAGNOSE-ERGEBNIS")
    print("="*30)
    if is_unified:
        print("\n>>> KONTOTYP: Einheitliches Handelskonto (Unified Trading Account) <<<")
        print("\nBEFUND: Dies ist sehr wahrscheinlich die Ursache der Probleme. Der JaegerBot ist für das 'Klassische Konto' ausgelegt. Die API-Logik für das einheitliche Konto ist anders, was zu Fehlern bei der Order-Platzierung führt.")
    else:
        print("\n>>> KONTOTYP: Klassisches Konto (Classic Account) <<<")
        print("\nBEFUND: Das ist der korrekte Kontotyp. Wenn der Fehler weiterhin besteht, müssen wir die Ursache woanders suchen.")
    print("="*30)

    # Gib die rohe 'info'-Struktur aus, damit wir sie manuell prüfen können
    print("\n--- Rohdaten der API-Antwort ('info'-Sektion) ---")
    pprint.pprint(info)

except Exception as e:
    print(f"\nEin Fehler ist aufgetreten: {e}")
