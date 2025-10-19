# master_runner.py
import json
import subprocess
import sys
import os
import time

# Pfad anpassen, damit die utils importiert werden können
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = SCRIPT_DIR
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from jaegerbot.utils.exchange import Exchange

def main():
    """
    Der Master Runner für den JaegerBot (Voll-Dynamisches Kapital).
    - Liest die settings.json, um den Modus (Autopilot/Manuell) zu bestimmen.
    - Startet für jede als "active" markierte Strategie einen separaten run.py Prozess
      innerhalb der korrekten virtuellen Umgebung.
    """
    settings_file = os.path.join(SCRIPT_DIR, 'settings.json')
    optimization_results_file = os.path.join(SCRIPT_DIR, 'artifacts', 'results', 'optimization_results.json')
    bot_runner_script = os.path.join(SCRIPT_DIR, 'src', 'jaegerbot', 'strategy', 'run.py')
    secret_file = os.path.join(SCRIPT_DIR, 'secret.json')

    # Finde den exakten Pfad zum Python-Interpreter in der virtuellen Umgebung
    python_executable = os.path.join(SCRIPT_DIR, '.venv', 'bin', 'python3')
    if not os.path.exists(python_executable):
        print(f"Fehler: Python-Interpreter in der venv nicht gefunden unter {python_executable}")
        return

    print("=======================================================")
    print("JaegerBot Master Runner v3.3 (final)")
    print("=======================================================")

    try:
        with open(settings_file, 'r') as f:
            settings = json.load(f)

        with open(secret_file, 'r') as f:
            secrets = json.load(f)
        
        if not secrets.get('jaegerbot'):
            print("Fehler: Kein 'jaegerbot'-Account in secret.json gefunden.")
            return
        main_account_config = secrets['jaegerbot'][0]

        print(f"Frage Kontostand für Account '{main_account_config.get('name', 'Standard')}' ab...")
        # (Kapitalabfrage wird hier nicht mehr benötigt, da sie in run.py stattfindet)
        
        live_settings = settings.get('live_trading_settings', {})
        use_autopilot = live_settings.get('use_auto_optimizer_results', False)
        
        strategy_list = []
        if use_autopilot:
            print("Modus: Autopilot. Lese Strategien aus den Optimierungs-Ergebnissen...")
            with open(optimization_results_file, 'r') as f:
                strategy_config = json.load(f)
            strategy_list = strategy_config.get('optimal_portfolio', [])
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

            symbol, timeframe, use_macd = None, None, None

            if use_autopilot and isinstance(strategy_info, str):
                try:
                    use_macd = '_macd' in strategy_info
                    base_name = strategy_info.replace('config_', '').replace('.json', '').replace('_macd', '')
                    parts = base_name.split('_')
                    timeframe = parts[-1]
                    symbol_base = parts[0].replace('USDTUSDT', '')
                    symbol = f"{symbol_base}/USDT:USDT"
                except Exception as e:
                    print(f"Warnung: Konnte Autopilot-Strategie '{strategy_info}' nicht verarbeiten. Fehler: {e}")
                    continue
            
            elif isinstance(strategy_info, dict):
                symbol = strategy_info.get('symbol')
                timeframe = strategy_info.get('timeframe')
                use_macd = strategy_info.get('use_macd_filter', False)
            
            if not all([symbol, timeframe, use_macd is not None]):
                print(f"Warnung: Unvollständige Strategie-Info: {strategy_info}. Überspringe.")
                continue

            print(f"\n--- Starte Bot für: {symbol} ({timeframe}) ---")
            print(f"    - MACD-Filter-Version: {'JA' if use_macd else 'NEIN'}")
            
            # --- HIER IST DIE FINALE KORREKTUR ---
            # Der --use_macd Parameter wird jetzt korrekt an den Befehl übergeben
            command = [
                python_executable,
                bot_runner_script,
                "--symbol", symbol,
                "--timeframe", timeframe,
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
