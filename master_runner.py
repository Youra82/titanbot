# master_runner.py
import json
import subprocess
import sys
import os
import time
import re

# Pfad anpassen, damit die utils importiert werden können
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = SCRIPT_DIR
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

# *** Geändert: Importpfad ***
from titanbot.utils.exchange import Exchange

def main():
    """
    Der Master Runner für den TitanBot (Voll-Dynamisches Kapital).
    - Liest die settings.json, um den Modus (Autopilot/Manuell) zu bestimmen.
    - Startet für jede als "active" markierte Strategie einen separaten run.py Prozess
      innerhalb der korrekten virtuellen Umgebung.
    """
    settings_file = os.path.join(SCRIPT_DIR, 'settings.json')
    optimization_results_file = os.path.join(SCRIPT_DIR, 'artifacts', 'results', 'optimization_results.json')
    # *** Geändert: Pfad zum Bot-Runner ***
    bot_runner_script = os.path.join(SCRIPT_DIR, 'src', 'titanbot', 'strategy', 'run.py')
    secret_file = os.path.join(SCRIPT_DIR, 'secret.json')

    # Finde den exakten Pfad zum Python-Interpreter in der virtuellen Umgebung
    python_executable = os.path.join(SCRIPT_DIR, '.venv', 'bin', 'python3')
    if not os.path.exists(python_executable):
        print(f"Fehler: Python-Interpreter in der venv nicht gefunden unter {python_executable}")
        return

    print("=======================================================")
    # *** Geändert: Name ***
    print("TitanBot Master Runner v1.0")
    print("=======================================================")

    # Zeige sofort, ob gerade eine automatische Optimierung läuft
    inprog = os.path.join(SCRIPT_DIR, 'data', 'cache', '.optimization_in_progress')
    if os.path.exists(inprog):
        try:
            ts = open(inprog, 'r', encoding='utf-8').read().strip()
            print(f"INFO: Automatische Optimierung läuft (gestartet: {ts})")
        except Exception:
            print("INFO: Automatische Optimierung läuft (Startzeit unbekannt)")
    else:
        print("INFO: Keine laufende automatische Optimierung gefunden.")

    try:
        with open(settings_file, 'r') as f:
            settings = json.load(f)

        with open(secret_file, 'r') as f:
            secrets = json.load(f)

        # *** Geändert: Account-Name (optional) ***
        if not secrets.get('titanbot'):
            print("Fehler: Kein 'titanbot'-Account in secret.json gefunden.")
            return
        main_account_config = secrets['titanbot'][0]

        print(f"Frage Kontostand für Account '{main_account_config.get('name', 'Standard')}' ab...")
        
        live_settings = settings.get('live_trading_settings', {})
        use_autopilot = live_settings.get('use_auto_optimizer_results', False)

        strategy_list = []
        if use_autopilot:
            print("Modus: Autopilot. Lese Strategien aus den Optimierungs-Ergebnissen...")
            with open(optimization_results_file, 'r') as f:
                strategy_config = json.load(f)
            strategy_list = strategy_config.get('optimal_portfolio', [])
            # DEBUG: Zeige, was aus optimization_results.json geladen wurde
            print(f"DEBUG: optimization_results -> {strategy_list}")
            configs_check_dir = os.path.join(SCRIPT_DIR, 'src', 'titanbot', 'strategy', 'configs')
            print(f"DEBUG: configs_dir exists: {os.path.isdir(configs_check_dir)} ({configs_check_dir})")
        else:
            print("Modus: Manuell. Lese Strategien aus den manuellen Einstellungen...")
            strategy_list = live_settings.get('active_strategies', [])

        if not strategy_list:
            print("Keine aktiven Strategien zum Ausführen gefunden.")
            return

        print("=======================================================")

        for strategy_info in strategy_list:
            if isinstance(strategy_info, dict) and not strategy_info.get("active", True):
                symbol = strategy_info.get('symbol', 'N/A')
                timeframe = strategy_info.get('timeframe', 'N/A')
                print(f"\n--- Überspringe inaktive Strategie: {symbol} ({timeframe}) ---")
                continue

            symbol, timeframe, use_macd = None, None, None  # use_macd wird für SMC nicht verwendet

            if use_autopilot and isinstance(strategy_info, str):
                # strategy_info ist ein Config-Dateiname aus den Optimizer-Ergebnissen
                config_name = strategy_info
                configs_dir = os.path.join(SCRIPT_DIR, 'src', 'titanbot', 'strategy', 'configs')
                config_path = os.path.join(configs_dir, config_name)

                if os.path.exists(config_path):
                    try:
                        with open(config_path, 'r', encoding='utf-8') as cf:
                            cfg = json.load(cf)
                        symbol = cfg.get('market', {}).get('symbol')
                        timeframe = cfg.get('market', {}).get('timeframe')
                        use_macd = cfg.get('strategy', {}).get('use_macd_filter', False)
                    except Exception as e:
                        print(f"Warnung: Konnte Config '{config_name}' nicht lesen: {e}. Überspringe.")
                else:
                    # Fallback: versuche Symbol/Timeframe aus dem Dateinamen zu extrahieren
                    m = re.match(r'config_([A-Z0-9]+)USDTUSDT_(\w+)\.json', config_name)
                    if m:
                        base = m.group(1)
                        symbol = f"{base}/USDT:USDT"
                        timeframe = m.group(2)
                        use_macd = False
                    else:
                        print(f"Warnung: Unbekanntes Autopilot-Config-Format: {config_name}")

            elif isinstance(strategy_info, dict):
                symbol = strategy_info.get('symbol')
                timeframe = strategy_info.get('timeframe')
                # use_macd wird nicht mehr benötigt, aber wir müssen einen
                # Dummy-Wert übergeben, da run.py es erwartet
                use_macd = strategy_info.get('use_macd_filter', False)

            if not all([symbol, timeframe, use_macd is not None]):
                print(f"Warnung: Unvollständige Strategie-Info: {strategy_info}. Überspringe.")
                continue

            print(f"\n--- Starte Bot für: {symbol} ({timeframe}) ---")

            command = [
                python_executable,
                bot_runner_script,
                "--symbol", symbol,
                "--timeframe", timeframe,
                # Wir übergeben 'use_macd' als Dummy-Argument, da 'run.py' es erwartet
                "--use_macd", str(use_macd) 
            ]

            subprocess.Popen(command)
            time.sleep(2)

    except FileNotFoundError as e:
        print(f"Fehler: Eine wichtige Datei wurde nicht gefunden: {e}")
    except Exception as e:
        print(f"Ein unerwarteter Fehler im Master Runner ist aufgetreten: {e}")

if __name__ == "__main__":
    main()
