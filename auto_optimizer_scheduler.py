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
from datetime import datetime, date, time as dtime, timedelta, timezone

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
PORTFOLIO_SCRIPT = os.path.join(ROOT, 'run_portfolio_optimizer.py')

# Dedicated, single-line trigger/log file for clear start/skip/finish entries
TRIGGER_LOG = os.path.join(ROOT, 'logs', 'auto_optimizer_trigger.log')


def _format_duration(seconds: int) -> str:
    """Format seconds into a human-readable duration string."""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s"
    else:
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h}h {m}m"


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
    """Create an "in-progress" marker with PID and ISO timestamp (JSON) and
    write a lightweight JSON status file that other processes (master_runner)
    can read to show live progress.
    """
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(IN_PROGRESS_FILE, 'w', encoding='utf-8') as f:
            json.dump({'pid': os.getpid(), 'started': datetime.now().isoformat()}, f)
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


def _is_stale_in_progress(max_hours: int = 24) -> bool:
    """Returns True if the in-progress marker is stale.
    Stale means: the process (PID) is no longer running, OR the marker is
    older than max_hours (fallback if no PID stored).
    """
    if not os.path.exists(IN_PROGRESS_FILE):
        return False
    try:
        content = open(IN_PROGRESS_FILE, 'r', encoding='utf-8').read().strip()
        try:
            data = json.loads(content)
            pid = data.get('pid')
            started = data.get('started', '')
        except (json.JSONDecodeError, ValueError):
            # Legacy: plain ISO timestamp text
            pid = None
            started = content

        # PID check: if PID stored, check if process is still alive
        if pid:
            try:
                os.kill(int(pid), 0)   # signal 0 = existence check
                return False           # process alive → not stale
            except (ProcessLookupError, OSError):
                return True            # process dead → stale

        # No PID stored (legacy format): use pgrep to check for running scheduler
        try:
            import subprocess as _sp
            r = _sp.run(['pgrep', '-f', 'auto_optimizer_scheduler.py'],
                        capture_output=True, timeout=3)
            if r.returncode == 0:
                return False   # scheduler process found → not stale
            return True        # no scheduler process running → stale
        except Exception:
            pass

        # Final fallback: age check
        if started:
            try:
                started_dt = datetime.fromisoformat(started)
                age_hours = (datetime.now() - started_dt).total_seconds() / 3600
                return age_hours > max_hours
            except Exception:
                pass
        return True   # can't determine → treat as stale
    except Exception:
        return True   # error reading → treat as stale


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


def _interval_to_minutes(schedule: dict) -> int:
    """Liest das Intervall aus schedule und gibt es in Minuten zurück.
    Unterstützt das neue Format {"value": N, "unit": "minutes|hours|days|weeks"}
    sowie das alte Format interval_days als Fallback."""
    interval = schedule.get('interval', {})
    if interval and isinstance(interval, dict):
        value = int(interval.get('value', 0) or 0)
        unit = interval.get('unit', 'days').lower().rstrip('s')  # "days" → "day", "hours" → "hour"
        if unit in ('minute', 'min', 'm'):
            return value
        if unit in ('hour', 'h'):
            return value * 60
        if unit in ('day', 'd'):
            return value * 60 * 24
        if unit in ('week', 'w'):
            return value * 60 * 24 * 7
        return value * 60 * 24  # fallback: als Tage interpretieren
    # Legacy: interval_days
    legacy = int(schedule.get('interval_days', 0) or 0)
    return legacy * 60 * 24


def compute_last_scheduled_datetime(schedule: dict, now: datetime) -> datetime:
    # schedule: { day_of_week: 0..6 | None, hour: 0-23, minute: 0-59, interval: {value, unit} }
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
    interval_minutes = _interval_to_minutes(schedule)

    # --- Reines Intervall-Scheduling (Minuten/Stunden, < 1 Tag) ---
    # day_of_week/hour sind irrelevant; nur Abstand seit letztem Lauf zählt.
    if 0 < interval_minutes < 1440:
        if not last_run:
            return True, f'kein letzter Lauf (Intervall-Modus: {interval_minutes}min)'
        delta_minutes = (now - last_run).total_seconds() / 60
        if delta_minutes < interval_minutes:
            return False, f'zu frueh: {delta_minutes:.0f}min seit letztem Lauf < {interval_minutes}min'
        return True, f'interval={interval_minutes}min'

    # --- Tages-/Wochen-Scheduling: day_of_week + hour als Anker ---
    scheduled_dt = compute_last_scheduled_datetime(schedule, now)

    if now < scheduled_dt:
        return False, f'Next scheduled time not reached (scheduled={scheduled_dt.isoformat()})'

    # If last run is present and already ran after scheduled_dt -> skip
    if last_run and last_run >= scheduled_dt:
        return False, f'already ran for this scheduled occurrence (last_run={last_run.isoformat()})'

    # If interval is set and last_run is too recent -> skip
    if interval_minutes > 0 and last_run:
        delta_minutes = (now - last_run).total_seconds() / 60
        if delta_minutes < interval_minutes:
            delta_h = int(delta_minutes // 60)
            interval_h = int(interval_minutes // 60)
            return False, f'last run {delta_h}h ago < interval ({interval_h}h = {interval_minutes}min)'

    return True, f'should run (scheduled_dt={scheduled_dt.isoformat()}, interval={interval_minutes}min)'


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
    settings   = load_settings()
    opt        = settings.get('optimization_settings', {})
    capital    = str(opt.get('start_capital', 100))
    max_dd     = str(opt.get('constraints', {}).get('max_drawdown_pct', 30))
    start_date = opt.get('start_date', 'auto')
    end_date   = opt.get('end_date',   'auto')
    cmd = [sys.executable, PORTFOLIO_SCRIPT,
           '--capital', capital, '--max-dd', max_dd, '--auto-write']
    if start_date not in ('auto', '', None):
        cmd += ['--start-date', start_date]
    if end_date not in ('auto', '', None):
        cmd += ['--end-date', end_date]
    _write_trigger_log(f"AUTO-OPTIMIZER PORTFOLIO_OPTIMIZER_START capital={capital} max_dd={max_dd}")
    result = subprocess.run(cmd, cwd=ROOT)
    _write_trigger_log(f"AUTO-OPTIMIZER PORTFOLIO_OPTIMIZER_EXIT rc={result.returncode}")
    return result.returncode


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

            # mark in-progress; reset start-notify sentinel so jeder neue Lauf eine Nachricht sendet
            _set_in_progress()
            start_notify_file = os.path.join(CACHE_DIR, '.optimization_start_notified')
            try:
                if os.path.exists(start_notify_file):
                    os.remove(start_notify_file)
            except Exception:
                pass
            if notify:
                start_msg = (
                    f"🔍 titanbot Portfolio-Optimizer GESTARTET\n"
                    f"Start: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"Führt frische Backtests aller Configs durch und wählt bestes Portfolio."
                )
                sent = _send_telegram_message(start_msg)
                if not sent:
                    # one quick retry for transient network/API failures
                    try:
                        import time as _time
                        _time.sleep(3)
                        sent = _send_telegram_message(start_msg + ' (retry)')
                    except Exception:
                        sent = False
                if sent:
                    try:
                        os.makedirs(CACHE_DIR, exist_ok=True)
                        with open(start_notify_file, 'w', encoding='utf-8') as _sn:
                            _sn.write(datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'))
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

            if rc == 0:
                write_last_run(datetime.now())
                _write_trigger_log(f"AUTO-OPTIMIZER FINISH result=success elapsed_s={elapsed:.1f}")
                print('Portfolio optimizer finished successfully; updated last-run timestamp.')

                if notify:
                    dur_str = _format_duration(int(elapsed))
                    try:
                        updated = load_settings()
                        active = [s for s in updated.get('live_trading_settings', {})
                                  .get('active_strategies', []) if s.get('active')]
                        msg_lines = [f"✅ titanbot Portfolio-Optimizer abgeschlossen (Dauer: {dur_str})"]
                        if active:
                            msg_lines.append(f"\n✔ Aktives Portfolio ({len(active)} Strategie(n)):")
                            for s in active:
                                sym_short = s['symbol'].split('/')[0]
                                msg_lines.append(f"• {sym_short}/{s['timeframe']}")
                        _send_telegram_message('\n'.join(msg_lines))
                    except Exception:
                        _send_telegram_message(f"✅ titanbot Portfolio-Optimizer abgeschlossen (Dauer: {dur_str})")
            else:
                _write_trigger_log(f"AUTO-OPTIMIZER FINISH result=error code={rc} elapsed_s={elapsed:.1f}")
                print(f'Portfolio optimizer exited with return code {rc}')
                if notify:
                    _send_telegram_message(f'❌ titanbot Portfolio-Optimizer FEHLER (rc={rc})')

            # clear start-notify sentinel
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
