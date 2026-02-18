# master_runner.py
import json
import subprocess
import sys
import os
import time
import re
import threading
import runpy
import shutil

# Pfad anpassen, damit die utils importiert werden können
from datetime import datetime
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
        log_file = os.path.join(SCRIPT_DIR, 'logs', 'optimizer_output.log')
        
        if os.path.exists(optimizer_script):
            # Stelle sicher, dass logs/ Verzeichnis existiert
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            
            # Starte den Optimizer SYNCHRON (wartet auf Ende)
            # So wird die Telegram-Nachricht garantiert gesendet bevor wir weitermachen
            print(f"    Starte Optimizer im Hintergrund...")
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            with open(log_file, 'a') as log:
                # Schreibe klaren Start-Eintrag inkl. Grund in die Logdatei
                log.write(f"[{timestamp}] MasterRunner: Starte Auto-Optimizer — Grund: Geplanter Zeitpunkt erreicht\n")
                # Starte als Hintergrundprozess - Bots haben Priorität!
                subprocess.Popen(
                    [python_executable, optimizer_script, '--force'],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    cwd=SCRIPT_DIR,  # Wichtig: Arbeitsverzeichnis setzen!
                    start_new_session=True  # Läuft unabhängig weiter
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

    # Bestimme den passenden Python-Interpreter für diese Plattform (robust)
    def _find_python_exec():
        candidates = [
            os.path.join(SCRIPT_DIR, '.venv', 'Scripts', 'python.exe'),
            os.path.join(SCRIPT_DIR, '.venv', 'bin', 'python3'),
            sys.executable,
            shutil.which('python3') or '',
            shutil.which('python') or ''
        ]
        checked = set()
        for c in candidates:
            if not c or c in checked:
                continue
            checked.add(c)
            # Absolute path exists?
            if os.path.isabs(c) and os.path.exists(c):
                try:
                    proc = subprocess.run([c, '-c', 'import sys; print(1)'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                    if proc.returncode == 0:
                        return c
                except Exception:
                    continue
            else:
                found = shutil.which(c)
                if found:
                    try:
                        proc = subprocess.run([found, '-c', 'import sys; print(1)'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                        if proc.returncode == 0:
                            return found
                    except Exception:
                        continue
        return None

    python_executable = _find_python_exec()
    if not python_executable:
        print("Fehler: Kein lauffähiger Python-Interpreter für das Projekt gefunden (.venv oder system python).")
        return

    print("=======================================================")
    # *** Geändert: Name ***
    print("TitanBot Master Runner v1.0")
    print("=======================================================")

    # ----------------------
    # AUTO-OPTIMIZER STATUS
    # ----------------------
    try:
        auto_should_run = False
        auto_reason = 'unknown'
        sched_out = ''
        sched_err = ''
        try:
            # Use the scheduler's should_run logic locally to avoid spawning processes
            import auto_optimizer_scheduler as scheduler_mod
            last_run = None
            lr_path = os.path.join(SCRIPT_DIR, 'data', 'cache', '.last_optimization_run')
            if os.path.exists(lr_path):
                try:
                    last_run = datetime.fromisoformat(open(lr_path, 'r', encoding='utf-8').read().strip())
                except Exception:
                    last_run = None
            with open(settings_file, 'r', encoding='utf-8') as _sf:
                sched_settings = json.load(_sf)
            do_run, reason = scheduler_mod.should_run(sched_settings, last_run, datetime.now())
            auto_should_run = bool(do_run)
            auto_reason = reason
            sched_out = f"decision={'RUN' if do_run else 'SKIP'} reason={reason}"
        except Exception as e:
            sched_out = ''
            sched_err = f'ERROR computing scheduler check: {e}'

        # Sehr knappe Auto‑Optimizer‑Statusmeldung (nur nötigste Info)
        try:
            if auto_should_run:
                print(f"AUTOOPTIMIERUNG NÖTIG — Grund: {auto_reason}")
                print("Autooptimierung wird gestartet.")
            else:
                print(f"AUTOOPTIMIERUNG NICHT NÖTIG — Grund: {auto_reason}")
                print("Autooptimierung wird nicht gestartet.")
        except Exception:
            # Fallback minimal
            try:
                print(f"AUTO-OPTIMIZER: {'NEEDED' if auto_should_run else 'NOT DUE'} — reason={auto_reason}")
            except Exception:
                pass

    except Exception:
        # non-fatal — continue with master runner
        pass

    # Auto-optimizer runtime details are intentionally suppressed here to keep
    # master_runner console output concise. Logs still contain full details.

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

            try:
                subprocess.Popen(command)
            except Exception as e:
                print(f"WARN: could not start bot with '{command[0]}': {e} — falling back to sys.executable")
                try:
                    fallback_cmd = [sys.executable, bot_runner_script, "--symbol", symbol, "--timeframe", timeframe, "--use_macd", str(use_macd)]
                    subprocess.Popen(fallback_cmd)
                except Exception as e2:
                    print(f"ERROR: fallback start also failed: {e2}")
            time.sleep(2)

        # --- Auto-Optimizer: falls der 'last run' Cache gelöscht wurde, starte Scheduler im Forced-Modus ---
        try:
            opt_settings = settings.get('optimization_settings', {})
            if opt_settings.get('enabled', False):
                cache_file = os.path.join(SCRIPT_DIR, 'data', 'cache', '.last_optimization_run')
                inprog_file = os.path.join(SCRIPT_DIR, 'data', 'cache', '.optimization_in_progress')
                # Wenn Cache fehlt und kein Optimizer bereits läuft → erzwungener Start
                if (not os.path.exists(cache_file)) and (not os.path.exists(inprog_file)):
                    print(f"INFO: {cache_file} fehlt — trigger Auto-Optimizer (forced).")

                    # Schreibe eine eindeutige, einzeilige Trigger-Info in das Trigger-Log
                    try:
                        logs_dir = os.path.join(SCRIPT_DIR, 'logs')
                        os.makedirs(logs_dir, exist_ok=True)
                        entry = f"{datetime.now().isoformat()} MASTER_RUNNER TRIGGER reason=cache_missing\n"

                        # primary trigger log
                        with open(os.path.join(logs_dir, 'auto_optimizer_trigger.log'), 'a', encoding='utf-8') as _lf:
                            _lf.write(entry)

                        # mirror into master_runner_debug.log and optimizer_output.log for visibility
                        try:
                            with open(os.path.join(logs_dir, 'master_runner_debug.log'), 'a', encoding='utf-8') as _m:
                                _m.write(entry)
                        except Exception:
                            pass

                        try:
                            with open(os.path.join(logs_dir, 'optimizer_output.log'), 'a', encoding='utf-8') as _o:
                                _o.write(entry)
                        except Exception:
                            pass

                        # UND: sofort in die Konsole ausgeben (sichtbar beim master_runner Start)
                        try:
                            print(entry.strip())
                        except Exception:
                            pass

                    except Exception:
                        pass

                    scheduler_py = os.path.join(SCRIPT_DIR, 'auto_optimizer_scheduler.py')
                    if os.path.exists(scheduler_py):
                        os.makedirs(os.path.join(SCRIPT_DIR, 'logs'), exist_ok=True)
                        trigger_log = os.path.join(SCRIPT_DIR, 'logs', 'auto_optimizer_trigger.log')
                        print(f"INFO: Versuche Scheduler (forced) zu starten — logging -> {trigger_log}")

                        # Kandidaten für den Python-Interpreter (plattformübergreifend)
                        candidates = []
                        venv_unix = os.path.join(SCRIPT_DIR, '.venv', 'bin', 'python3')
                        venv_win = os.path.join(SCRIPT_DIR, '.venv', 'Scripts', 'python.exe')
                        for c in (venv_unix, venv_win, sys.executable, 'python3', 'python'):
                            # Nur Kandidaten aufnehmen, die existieren oder von PATH gefunden werden
                            if os.path.isabs(c) and os.path.exists(c):
                                candidates.append(c)
                            else:
                                which = shutil.which(c)
                                if which:
                                    candidates.append(which)

                        # Entferne Duplikate, behalte Reihenfolge
                        seen = set(); py_candidates = [x for x in candidates if not (x in seen or seen.add(x))]

                        started = False
                        lf = None
                        for py in py_candidates:
                            try:
                                lf = open(trigger_log, 'a', encoding='utf-8')
                                proc = subprocess.Popen([py, scheduler_py, '--force'],
                                                        cwd=SCRIPT_DIR,
                                                        stdout=lf,
                                                        stderr=subprocess.STDOUT,
                                                        start_new_session=True)
                                time.sleep(0.75)
                                if proc.poll() is None:
                                    started = True
                                    lf.flush(); lf.close()
                                    print(f'INFO: Scheduler gestartet mit {py} (PID {proc.pid}).')

                                    # Sende Telegram-Startmeldung mit Details (best-effort)
                                    try:
                                        secret_path = os.path.join(SCRIPT_DIR, 'secret.json')
                                        cfg_settings = {}
                                        if os.path.exists(settings_file):
                                            with open(settings_file, 'r', encoding='utf-8') as _sf:
                                                cfg_settings = json.load(_sf)
                                        syms = []
                                        tfs = []
                                        try:
                                            import auto_optimizer_scheduler as scheduler_mod
                                            syms = scheduler_mod.extract_symbols_timeframes(cfg_settings, 'symbols')
                                            tfs = scheduler_mod.extract_symbols_timeframes(cfg_settings, 'timeframes')
                                        except Exception:
                                            strategies = cfg_settings.get('live_trading_settings', {}).get('active_strategies', [])
                                            syms = sorted({s.get('symbol','').split('/')[0] for s in strategies if s.get('active', False)})
                                            tfs = sorted({s.get('timeframe') for s in strategies if s.get('active', False)})

                                        if os.path.exists(secret_path):
                                            with open(secret_path, 'r', encoding='utf-8') as sf:
                                                secret_data = json.load(sf)
                                            tg = secret_data.get('telegram', {})
                                            bot = tg.get('bot_token')
                                            chat = tg.get('chat_id')
                                            if bot and chat:
                                                from titanbot.utils.telegram import send_message
                                                send_message(bot, chat, (
                                                    f"🚀 Auto‑Optimizer STARTED (forced)\nSymbole: {', '.join(syms) if syms else 'auto'}\nTimeframes: {', '.join(tfs) if tfs else 'auto'}\nStart: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                                                ))
                                    except Exception:
                                        pass

                                    break
                                else:
                                    lf.flush(); lf.close()
                                    print(f'WARN: Start mit {py} schlug fehl (exit={proc.returncode}). Versuche nächsten Kandidaten...')
                                    continue
                            except Exception as e:
                                if lf:
                                    try: lf.close()
                                    except Exception: pass
                                print(f'WARN: Start mit {py} war nicht möglich: {e}')

                        # Fallback: in-proc Ausführung (sicherer, falls Subprocess scheitert)
                        if not started:
                            print('WARN: Alle subprocess-Startversuche fehlgeschlagen — fall back to in-process execution (daemon thread).')
                            try:
                                def _run_scheduler_inproc():
                                    try:
                                        runpy.run_path(scheduler_py, run_name='__main__')
                                    except Exception as ie:
                                        with open(trigger_log, 'a', encoding='utf-8') as _lf:
                                            _lf.write(f"INPROC-ERROR: {ie}\n")
                                t = threading.Thread(target=_run_scheduler_inproc, daemon=True)
                                t.start()
                                time.sleep(0.5)
                                print('INFO: Scheduler (inproc) gestartet (daemon thread).')
                                started = True
                            except Exception as ie:
                                print(f'ERROR: In-proc fallback fehlgeschlagen: {ie}')

                        # Endgültige Prüfung: wurde IN_PROGRESS gesetzt?
                        time.sleep(0.5)
                        if os.path.exists(inprog_file):
                            print('INFO: Auto-Optimizer wurde gestartet (in-progress marker vorhanden).')
                        else:
                            print('WARN: Scheduler-Start erfolgt, aber kein in-progress marker gefunden; prüfe logs/auto_optimizer_trigger.log')
                    else:
                        print('WARN: auto_optimizer_scheduler.py nicht gefunden; kann Optimizer nicht starten.')
        except Exception as _e:
            print(f'WARN: Auto-Optimizer Trigger fehlgeschlagen: {_e}')

    except FileNotFoundError as e:
        print(f"Fehler: Eine wichtige Datei wurde nicht gefunden: {e}")
    except Exception as e:
        print(f"Ein unerwarteter Fehler im Master Runner ist aufgetreten: {e}")

if __name__ == "__main__":
    main()
