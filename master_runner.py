# master_runner.py
import json
import subprocess
import sys
import os
import time
from datetime import datetime, timedelta

# Pfad anpassen, damit die utils importiert werden können
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = SCRIPT_DIR
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

# *** Geändert: Importpfad ***
from titanbot.utils.exchange import Exchange


def check_and_run_optimizer():
    """
    Prüft ob die automatische Optimierung fällig ist und führt sie ggf. aus.
    
    Wird bei jedem Cron-Job Aufruf einmal geprüft. Die Logik ist tolerant gegenüber
    Cron-Intervallen: Wenn der geplante Zeitpunkt in der Vergangenheit liegt (aber
    noch am selben Tag in der geplanten Stunde), wird die Optimierung gestartet.
    """
    now = datetime.now()
    
    try:
        settings_file = os.path.join(SCRIPT_DIR, 'settings.json')
        with open(settings_file, 'r') as f:
            settings = json.load(f)
        
        opt_settings = settings.get('optimization_settings', {})
        
        # Prüfe ob aktiviert
        if not opt_settings.get('enabled', False):
            return False
        
        schedule = opt_settings.get('schedule', {})
        day_of_week = schedule.get('day_of_week', 0)
        hour = schedule.get('hour', 3)
        minute = schedule.get('minute', 0)
        interval_days = schedule.get('interval_days', 7)
        
        # Prüfe ob heute der richtige Tag ist
        if now.weekday() != day_of_week:
            return False
        
        # Prüfe ob wir in der geplanten Stunde sind (oder danach, aber am gleichen Tag)
        if now.hour < hour:
            return False
        
        # Wenn wir in der richtigen Stunde sind, prüfe ob die Minute erreicht wurde
        if now.hour == hour and now.minute < minute:
            return False
        
        # Ab hier: Wir sind am richtigen Tag und der geplante Zeitpunkt ist erreicht oder überschritten
        
        # Prüfe ob heute schon gelaufen (oder innerhalb des Intervalls)
        cache_dir = os.path.join(SCRIPT_DIR, 'data', 'cache')
        cache_file = os.path.join(cache_dir, '.last_optimization_run')
        
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                last_run = datetime.fromtimestamp(int(f.read().strip()))
                
                # Wenn heute schon gelaufen, nicht nochmal
                if last_run.date() == now.date():
                    return False
                
                # Wenn innerhalb des Intervalls, nicht nochmal
                if (now - last_run).days < interval_days:
                    return False
        
        # Zeit für Optimierung!
        print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] 🔄 Auto-Optimizer: Geplanter Zeitpunkt erreicht!")
        print(f"    Geplant war: {['Mo','Di','Mi','Do','Fr','Sa','So'][day_of_week]} {hour:02d}:{minute:02d}")
        print(f"    Starte Optimierung...")
        
        python_executable = os.path.join(SCRIPT_DIR, '.venv', 'bin', 'python3')
        optimizer_script = os.path.join(SCRIPT_DIR, 'auto_optimizer_scheduler.py')
        
        if os.path.exists(optimizer_script):
            subprocess.Popen(
                [python_executable, optimizer_script, '--force'],
                stdout=open(os.path.join(SCRIPT_DIR, 'logs', 'optimizer_output.log'), 'a'),
                stderr=subprocess.STDOUT,
                start_new_session=True
            )
            return True
        else:
            print(f"    Fehler: {optimizer_script} nicht gefunden!")
            return False
        
    except Exception as e:
        print(f"Optimizer-Check Fehler: {e}")
        return False


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

            symbol, timeframe, use_macd = None, None, None # use_macd wird für SMC nicht verwendet

            if use_autopilot and isinstance(strategy_info, str):
                # ... (Diese Logik muss ggf. angepasst werden, wenn der Autopilot
                # ... (für SMC genutzt wird, aktuell ignoriert)
                pass 
            
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
    # EINMALIGER Auto-Optimizer Check beim Start (cron-kompatibel)
    check_and_run_optimizer()
    
    # Dann normale Bot-Starts
    main()
