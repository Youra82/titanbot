#!/usr/bin/env python3
"""
Auto Optimizer Scheduler for TitanBot
- Reads `optimization_settings.schedule` from `settings.json` and decides whether
  to start `run_pipeline_automated.sh`.
- Usage:
    python auto_optimizer_scheduler.py --check-only    # only evaluate, don't run
    python auto_optimizer_scheduler.py --force         # force a run now
    python auto_optimizer_scheduler.py --daemon        # keep checking (default interval 300s)

Behavior:
- Uses `data/cache/.last_optimization_run` to remember the last run time.
- Will trigger when the scheduled time passed and the last run is older than
  the scheduled occurrence (respects `interval_days`).

This is a lightweight, opinionated scheduler so you can run it from cron every
15 minutes or run it as a background daemon.
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, date, time as dtime, timedelta

# HTTP helper for Telegram notifications
try:
    import requests
except Exception:
    requests = None

ROOT = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(ROOT, 'settings.json')
CACHE_DIR = os.path.join(ROOT, 'data', 'cache')
LAST_RUN_FILE = os.path.join(CACHE_DIR, '.last_optimization_run')
# File created while the scheduler/optimizer is running
IN_PROGRESS_FILE = os.path.join(CACHE_DIR, '.optimization_in_progress')
PIPELINE_SCRIPT = os.path.join(ROOT, 'run_pipeline_automated.sh')

# Dedicated, single-line trigger/log file for clear start/skip/finish entries
TRIGGER_LOG = os.path.join(ROOT, 'logs', 'auto_optimizer_trigger.log')

def _write_trigger_log(line: str) -> None:
    """Append a single-line, timestamped entry to TRIGGER_LOG and also mirror
    it into the main `optimizer_output.log` and `master_runner_debug.log` so the
    trigger is immediately visible in the logs you usually open.
    """
    try:
        os.makedirs(os.path.dirname(TRIGGER_LOG), exist_ok=True)
        ts = datetime.now().isoformat()
        entry = f"{ts} {line}\n"

        # Primary trigger file (short, grep-friendly)
        with open(TRIGGER_LOG, 'a', encoding='utf-8') as f:
            f.write(entry)

        # Mirror into main logs for visibility (best-effort)
        try:
            opt_log = os.path.join(ROOT, 'logs', 'optimizer_output.log')
            os.makedirs(os.path.dirname(opt_log), exist_ok=True)
            with open(opt_log, 'a', encoding='utf-8') as f2:
                f2.write(entry)
        except Exception:
            pass

        try:
            mr_log = os.path.join(ROOT, 'logs', 'master_runner_debug.log')
            os.makedirs(os.path.dirname(mr_log), exist_ok=True)
            with open(mr_log, 'a', encoding='utf-8') as f3:
                f3.write(entry)
        except Exception:
            pass

        # Also print so the scheduler output and any captured stdout show the same line
        print(entry.strip())
    except Exception as _:
        # Don't fail the scheduler just because logging failed
        print(f"WARN: could not write trigger log: {line}")


def _set_in_progress() -> None:
    """Create an "in-progress" marker with an ISO timestamp and write a
    lightweight JSON status file that other processes (master_runner) can
    read to show live progress.
    """
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(IN_PROGRESS_FILE, 'w', encoding='utf-8') as f:
            f.write(datetime.now().isoformat())
        # write a starter status file
        status_file = os.path.join(CACHE_DIR, '.optimization_status.json')
        try:
            with open(status_file, 'w', encoding='utf-8') as sf:
                json.dump({'status': 'starting', 'started_at': datetime.now().isoformat()}, sf)
        except Exception:
            pass
        print(f'DEBUG: wrote in-progress marker {IN_PROGRESS_FILE}')
    except Exception as e:
        print(f'WARN: could not write in-progress marker: {e}')


def _clear_in_progress() -> None:
    try:
        if os.path.exists(IN_PROGRESS_FILE):
            os.remove(IN_PROGRESS_FILE)
            print(f'DEBUG: cleared in-progress marker {IN_PROGRESS_FILE}')
        # remove or update status file
        status_file = os.path.join(CACHE_DIR, '.optimization_status.json')
        try:
            if os.path.exists(status_file):
                os.remove(status_file)
        except Exception:
            pass
    except Exception as e:
        print(f'WARN: could not clear in-progress marker: {e}')


def _read_in_progress_ts() -> str | None:
    try:
        if os.path.exists(IN_PROGRESS_FILE):
            return open(IN_PROGRESS_FILE, 'r', encoding='utf-8').read().strip()
    except Exception:
        return None
    return None


def load_settings() -> dict:
    with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def read_last_run() -> datetime | None:
    try:
        with open(LAST_RUN_FILE, 'r', encoding='utf-8') as f:
            text = f.read().strip()
            return datetime.fromisoformat(text)
    except Exception:
        return None


def write_last_run(ts: datetime) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(LAST_RUN_FILE, 'w', encoding='utf-8') as f:
        f.write(ts.isoformat())
    try:
        _write_trigger_log(f"AUTO-OPTIMIZER LAST_RUN updated={ts.isoformat()}")
    except Exception:
        pass


def compute_last_scheduled_datetime(schedule: dict, now: datetime) -> datetime:
    # schedule: { day_of_week: 0..6 | None, hour: 0-23, minute: 0-59, interval_days: n }
    dow = schedule.get('day_of_week')
    hour = int(schedule.get('hour', 0))
    minute = int(schedule.get('minute', 0))

    if dow is None:
        # daily schedule -> today's scheduled time
        scheduled_date = now.date()
    else:
        # find the most recent date with that weekday on or before today
        days_ago = (now.weekday() - int(dow)) % 7
        scheduled_date = (now - timedelta(days=days_ago)).date()

    scheduled_dt = datetime.combine(scheduled_date, dtime(hour=hour, minute=minute))
    return scheduled_dt


def should_run(settings: dict, last_run: datetime | None, now: datetime) -> tuple[bool, str]:
    opt = settings.get('optimization_settings', {})
    if not opt.get('enabled', False):
        return False, 'optimization_settings.enabled is false'

    schedule = opt.get('schedule', {})
    interval_days = int(schedule.get('interval_days', 0) or 0)

    scheduled_dt = compute_last_scheduled_datetime(schedule, now)

    if now < scheduled_dt:
        return False, f'Next scheduled time not reached (scheduled={scheduled_dt.isoformat()})'

    # If last run is present and already ran after scheduled_dt -> skip
    if last_run and last_run >= scheduled_dt:
        return False, f'already ran for this scheduled occurrence (last_run={last_run.isoformat()})'

    # If interval_days is set and last_run is too recent -> skip
    if interval_days > 0 and last_run:
        delta_days = (now.date() - last_run.date()).days
        if delta_days < interval_days:
            return False, f'last run {delta_days} days ago < interval_days ({interval_days})'

    return True, f'should run (scheduled_dt={scheduled_dt.isoformat()}, interval_days={interval_days})'


def _send_telegram_message(text: str) -> bool:
    """Send a Telegram message using bot token/chat_id from secret.json.
    Returns True if sent (or at least attempted), False otherwise.
    """
    secret_path = os.path.join(ROOT, 'secret.json')
    try:
        with open(secret_path, 'r', encoding='utf-8') as f:
            sec = json.load(f)
        tg = sec.get('telegram', {})
        bot = tg.get('bot_token')
        chat = tg.get('chat_id')
        if not bot or not chat:
            print('INFO: Telegram not configured in secret.json (bot_token/chat_id missing)')
            return False
    except Exception as e:
        print(f'INFO: secret.json not available or unreadable: {e}')
        return False

    payload = {'chat_id': chat, 'text': text}
    url = f'https://api.telegram.org/bot{bot}/sendMessage'

    if requests:
        try:
            r = requests.post(url, data=payload, timeout=10)
            if r.status_code == 200:
                return True
            else:
                print(f'WARN: Telegram API returned status {r.status_code}: {r.text}')
                return False
        except Exception as e:
            print(f'WARN: Exception while sending Telegram message: {e}')
            return False
    else:
        # fallback to curl if requests not available
        try:
            subprocess.run(['curl', '-s', '-X', 'POST', url, '-d', f"chat_id={chat}", '-d', f"text={text}"], check=False)
            return True
        except Exception as e:
            print(f'WARN: Could not send Telegram message (curl fallback failed): {e}')
            return False


def run_pipeline() -> int:
    # Execute the existing run_pipeline_automated.sh using bash.
    # Use list form for subprocess to avoid shell quoting issues on Windows (PowerShell).
    if not os.path.exists(PIPELINE_SCRIPT):
        print(f'ERROR: pipeline script not found: {PIPELINE_SCRIPT}')
        return 2

    # Prefer passing args as a list to subprocess to prevent the outer shell from
    # mangling quotes (problem observed when running under PowerShell on Windows).
    # On Windows, try to map the Windows path to a bash-accessible path (WSL or MSYS).
    bash_cmd = None

    # Helper: test whether 'bash -lc "cd '<path>' && pwd"' succeeds
    def _bash_cd_ok(path_candidate: str) -> bool:
        try:
            rc = subprocess.run(['bash', '-lc', f"cd '{path_candidate}' && pwd"], shell=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            return rc.returncode == 0
        except Exception:
            return False

    if os.name == 'nt':
        from pathlib import Path
        p = Path(ROOT)
        drive = p.drive.rstrip(':').lower() if p.drive else ''
        rest = p.as_posix().split(':', 1)[-1] if ':' in p.as_posix() else p.as_posix()
        candidates = []
        if drive:
            candidates.append(f"/mnt/{drive}{rest}")   # WSL style
            candidates.append(f"/{drive}{rest}")       # MSYS/Git-Bash style
        candidates.append(ROOT)  # last resort: original Windows path

        for c in candidates:
            if _bash_cd_ok(c):
                bash_cmd = ['bash', '-lc', f"cd '{c}' && ./run_pipeline_automated.sh"]
                print(f'INFO: using bash cd candidate: {c}')
                break

    else:
        # POSIX environment — ROOT should be fine as-is
        bash_cmd = ['bash', '-lc', f"cd '{ROOT}' && ./run_pipeline_automated.sh"]

    if bash_cmd is None:
        print('WARN: could not determine a bash-accessible path for ROOT — will attempt direct shell execution')
        try:
            result = subprocess.run(f"cd {ROOT} && ./run_pipeline_automated.sh", shell=True)
            return result.returncode
        except Exception as e:
            print(f'ERROR: direct pipeline fallback failed: {e}')
            return 3

    print(f'Running pipeline (list form): {bash_cmd}')
    _write_trigger_log(f"AUTO-OPTIMIZER PIPELINE_EXEC method=bash cmd={bash_cmd}")

    try:
        result = subprocess.run(bash_cmd, shell=False)
        print(f'Pipeline exited with return code: {result.returncode}')
        _write_trigger_log(f"AUTO-OPTIMIZER PIPELINE_EXIT rc={result.returncode}")

        # Wenn der Bash-Aufruf fehlschlägt, versuchen wir eine Python-Direct-Invocation
        if result.returncode != 0:
            _write_trigger_log('AUTO-OPTIMIZER PIPELINE_WARNING Bash exit != 0 — attempting Python fallback')
            print('WARN: Bash pipeline failed — attempting direct Python fallback (invoke optimizer.py)')

            try:
                settings = load_settings()
                opt = settings.get('optimization_settings', {})

                # Resolve symbols
                syms = opt.get('symbols_to_optimize', 'auto')
                if isinstance(syms, list):
                    symbols_arg = ' '.join(syms)
                elif str(syms).lower() == 'auto':
                    # scan data/cache for available symbols
                    import glob, re
                    files = glob.glob(os.path.join(ROOT, 'data', 'cache', '*-USDT-USDT_*.csv'))
                    found = set()
                    for f in files:
                        m = re.search(r'([A-Z0-9]+)-USDT-USDT_', os.path.basename(f))
                        if m:
                            found.add(m.group(1))
                    if not found:
                        found = {'BTC','ETH','SOL','XRP','AAVE'}
                    symbols_arg = ' '.join(sorted(found))
                else:
                    symbols_arg = str(syms)

                # Resolve timeframes
                tfs = opt.get('timeframes_to_optimize', 'auto')
                if isinstance(tfs, list):
                    timeframes_arg = ' '.join(tfs)
                elif str(tfs).lower() == 'auto':
                    timeframes_arg = '5m 2h 4h 6h'
                else:
                    timeframes_arg = str(tfs)

                # Lookback / dates
                lb = opt.get('lookback_days', 'auto')
                try:
                    lookback_days = int(lb)
                except Exception:
                    lookback_days = 365
                from datetime import datetime, timedelta
                start_date = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
                end_date = datetime.now().strftime('%Y-%m-%d')

                # Other args
                jobs = int(opt.get('cpu_cores', -1))
                trials = int(opt.get('num_trials', 10))
                max_dd = float(opt.get('constraints', {}).get('max_drawdown_pct', 30))
                min_wr = float(opt.get('constraints', {}).get('min_win_rate_pct', 50))
                start_capital = float(opt.get('start_capital', 1000))
                min_pnl = float(opt.get('constraints', {}).get('min_pnl_pct', 0))
                mode = 'strict'

                optimizer_py = os.path.join(ROOT, 'src', 'titanbot', 'analysis', 'optimizer.py')
                if not os.path.exists(optimizer_py):
                    print(f'ERROR: optimizer.py not found at {optimizer_py} — cannot fallback')
                    return result.returncode

                # Prefer the project's venv Python if available
                venv_py_unix = os.path.join(ROOT, '.venv', 'bin', 'python3')
                venv_py_win = os.path.join(ROOT, '.venv', 'Scripts', 'python.exe')
                python_exec = None
                if os.path.exists(venv_py_unix):
                    python_exec = venv_py_unix
                elif os.path.exists(venv_py_win):
                    python_exec = venv_py_win
                else:
                    python_exec = sys.executable or 'python'

                cmd = [python_exec, optimizer_py,
                       '--symbols', symbols_arg,
                       '--timeframes', timeframes_arg,
                       '--start_date', start_date,
                       '--end_date', end_date,
                       '--jobs', str(jobs),
                       '--max_drawdown', str(max_dd),
                       '--start_capital', str(start_capital),
                       '--min_win_rate', str(min_wr),
                       '--trials', str(trials),
                       '--min_pnl', str(min_pnl),
                       '--mode', mode]

                print('Running direct optimizer fallback with interpreter:', python_exec)
                _write_trigger_log(f"AUTO-OPTIMIZER FALLBACK method=python interpreter={python_exec}")
                print('Running direct optimizer fallback:', ' '.join(map(str, cmd[:6])), '...')

                # If we're on Windows but the chosen interpreter is the unix venv python,
                # run it via 'bash -lc' using a /mnt/c/… path so WSL executes the venv python.
                if os.name == 'nt' and python_exec.endswith(os.path.join('.venv', 'bin', 'python3')):
                    try:
                        from pathlib import Path
                        pe = Path(python_exec)
                        op = Path(optimizer_py)
                        drive = pe.drive.rstrip(':').lower() if pe.drive else ''
                        rest_py = pe.as_posix().split(':', 1)[-1] if ':' in pe.as_posix() else pe.as_posix()
                        rest_op = op.as_posix().split(':', 1)[-1] if ':' in op.as_posix() else op.as_posix()
                        bash_venv = f"/mnt/{drive}{rest_py}"
                        bash_optimizer = f"/mnt/{drive}{rest_op}"
                        bash_cmd = ['bash', '-lc', f"'{bash_venv}' '{bash_optimizer}' --symbols \"{symbols_arg}\" --timeframes \"{timeframes_arg}\" --start_date {start_date} --end_date {end_date} --jobs {jobs} --max_drawdown {max_dd} --start_capital {start_capital} --min_win_rate {min_wr} --trials {trials} --min_pnl {min_pnl} --mode {mode}"]
                        print('INFO: executing venv python via WSL bash:', bash_cmd[2])
                        rc = subprocess.run(bash_cmd)
                        print('Direct (wsL-venv) optimizer exit code:', rc.returncode)
                        return rc.returncode
                    except Exception as e:
                        print('WARN: WSL venv invocation failed:', e)

                rc = subprocess.run(cmd)
                print('Direct optimizer exit code:', rc.returncode)
                return rc.returncode

            except Exception as e:
                print('ERROR: Python fallback failed:', e)
                return 4

        return result.returncode

    except FileNotFoundError:
        # 'bash' not available on PATH — try fallback to calling script directly
        _write_trigger_log('AUTO-OPTIMIZER PIPELINE_FALLBACK method=direct_shell')
        print('WARN: bash not found on PATH — attempting direct shell execution fallback')
        cmd = f"cd {ROOT} && ./run_pipeline_automated.sh"
        result = subprocess.run(cmd, shell=True)
        _write_trigger_log(f"AUTO-OPTIMIZER PIPELINE_EXIT rc={result.returncode}")
        return result.returncode
    except Exception as e:
        print(f'ERROR: Exception while running pipeline: {e}')
        return 3
    except Exception as e:
        print(f'ERROR: Exception while running pipeline: {e}')
        return 3


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument('--check-only', action='store_true', help="Only evaluate scheduler (don't run)")
    p.add_argument('--force', action='store_true', help="Force a run now and update last-run timestamp")
    p.add_argument('--daemon', action='store_true', help="Run scheduler loop (use --interval to change sleep)")
    p.add_argument('--interval', type=int, default=300, help="Daemon sleep interval in seconds")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    now = datetime.now()

    try:
        settings = load_settings()
    except Exception as e:
        print(f'ERROR: cannot load settings.json: {e}')
        return 3

    def check_and_maybe_run(force: bool = False) -> None:
        nonlocal now
        now = datetime.now()
        last_run = read_last_run()

        if force:
            # Write a single-line trigger entry with the reason = forced
            last_run = read_last_run()
            schedule = settings.get('optimization_settings', {}).get('schedule', {})
            _write_trigger_log(f"AUTO-OPTIMIZER START reason=forced scheduled={schedule} last_run={last_run}")

            print('Force run requested -> executing pipeline now')
            notify = settings.get('optimization_settings', {}).get('send_telegram_on_completion', False)

            # mark in-progress and notify ONCE (use sentinel)
            _set_in_progress()
            start_notify_file = os.path.join(CACHE_DIR, '.optimization_start_notified')
            if notify and (not os.path.exists(start_notify_file)):
                # try sending start notification and only create the sentinel if successful
                sent = _send_telegram_message('🚀 Automatische Optimierung (forced) wurde gestartet.')
                if not sent:
                    # one quick retry for transient network/API failures
                    try:
                        import time as _time
                        _time.sleep(3)
                        sent = _send_telegram_message('🚀 Automatische Optimierung (forced) wurde gestartet. (retry)')
                    except Exception:
                        sent = False
                if sent:
                    try:
                        os.makedirs(CACHE_DIR, exist_ok=True)
                        with open(start_notify_file, 'w', encoding='utf-8') as _sn:
                            _sn.write(datetime.utcnow().isoformat() + 'Z')
                    except Exception:
                        pass
                else:
                    _write_trigger_log('AUTO-OPTIMIZER NOTIFY start=forced result=failed')

            start_ts = datetime.now()
            try:
                rc = run_pipeline()
            finally:
                # always clear the in-progress marker so other components don't hang
                _clear_in_progress()

            elapsed = (datetime.now() - start_ts).total_seconds()

            # Read optimizer run summary (if present) to compose a richer completion message
            summary_path = os.path.join(SCRIPT_DIR, 'artifacts', 'results', 'last_optimizer_run.json')
            run_summary = None
            try:
                if os.path.exists(summary_path):
                    with open(summary_path, 'r', encoding='utf-8') as sf:
                        run_summary = json.load(sf)
            except Exception:
                run_summary = None

            if rc == 0:
                write_last_run(datetime.now())
                _write_trigger_log(f"AUTO-OPTIMIZER FINISH result=success elapsed_s={elapsed:.1f}")
                print('Pipeline finished successfully; updated last-run timestamp.')

            summary_path = os.path.join(SCRIPT_DIR, 'artifacts', 'results', 'last_optimizer_run.json')
            run_summary = None
            try:
                if os.path.exists(summary_path):
                    with open(summary_path, 'r', encoding='utf-8') as sf:
                        run_summary = json.load(sf)
            except Exception:
                run_summary = None

            if rc == 0:
                write_last_run(datetime.now())
                _write_trigger_log(f"AUTO-OPTIMIZER FINISH result=success elapsed_s={elapsed:.1f}")
                print('Pipeline finished successfully; updated last-run timestamp.')

                # Send a single completion message (only once per run)
                if notify:
                    comp_msg = '✅ Automatische Optimierung abgeschlossen.'
                    if run_summary:
                        saved = [t for t in run_summary.get('tasks', []) if t.get('saved')]
                        unchanged = [t for t in run_summary.get('tasks', []) if not t.get('saved')]
                        saved_list = ', '.join(f"{s['symbol']}({s['timeframe']})" for s in saved[:8])
                        comp_msg = (
                            f"✅ Auto‑Optimizer abgeschlossen (Dauer: {int(run_summary.get('duration_s', elapsed))}s)\n"
                            f"Gesamt: {len(run_summary.get('tasks', []))} Komb., Gespeichert: {len(saved)}, Unverändert: {len(unchanged)}\n"
                            f"Gespeicherte: {saved_list}"
                        )
                    _send_telegram_message(comp_msg)

                # clear start-notify sentinel
                try:
                    if os.path.exists(start_notify_file):
                        os.remove(start_notify_file)
                except Exception:
                    pass
            else:
                _write_trigger_log(f"AUTO-OPTIMIZER FINISH result=error code={rc} elapsed_s={elapsed:.1f}")
                print(f'Pipeline exited with return code {rc}')
                if notify:
                    _send_telegram_message(f'❌ Automatische Optimierung ist mit Fehlercode {rc} beendet.')

                try:
                    if os.path.exists(start_notify_file):
                        os.remove(start_notify_file)
                except Exception:
                    pass
        else:
            print('Not running at this time.')

    if args.check_only:
        check_and_maybe_run(force=False)
        return 0

    if args.force:
        check_and_maybe_run(force=True)
        return 0

    if args.daemon:
        print('Starting scheduler daemon... (use Ctrl-C to stop)')
        try:
            while True:
                check_and_maybe_run(force=False)
                import time as _time
                _time.sleep(args.interval)
        except KeyboardInterrupt:
            print('Scheduler daemon stopped by user')
            return 0

    # default: single check
    check_and_maybe_run(force=False)
    return 0


if __name__ == '__main__':
    sys.exit(main())
