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
PIPELINE_SCRIPT = os.path.join(ROOT, 'run_pipeline_automated.sh')


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
    # Execute the existing run_pipeline_automated.sh using bash
    if not os.path.exists(PIPELINE_SCRIPT):
        print(f'ERROR: pipeline script not found: {PIPELINE_SCRIPT}')
        return 2

    cmd = f"bash -lc 'cd {ROOT} && ./run_pipeline_automated.sh'"
    print(f'Running pipeline: {cmd}')
    result = subprocess.run(cmd, shell=True)
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
            print('Force run requested -> executing pipeline now')
            notify = settings.get('optimization_settings', {}).get('send_telegram_on_completion', False)
            if notify:
                _send_telegram_message('🚀 Automatische Optimierung (forced) wurde gestartet.')

            rc = run_pipeline()

            if rc == 0:
                write_last_run(datetime.now())
                print('Pipeline finished successfully; updated last-run timestamp.')
                if notify:
                    _send_telegram_message('✅ Automatische Optimierung (forced) ist abgeschlossen.')
            else:
                print(f'Pipeline exited with return code {rc}')
                if notify:
                    _send_telegram_message(f'❌ Automatische Optimierung (forced) ist mit Fehlercode {rc} beendet.')
            return

        do_run, reason = should_run(settings, last_run, now)
        print(f'Check at {now.isoformat()}: {reason}')
        if do_run:
            notify = settings.get('optimization_settings', {}).get('send_telegram_on_completion', False)
            if notify:
                _send_telegram_message('🚀 Automatische Optimierung wurde gestartet.')

            print('Condition met -> executing pipeline...')
            rc = run_pipeline()

            if rc == 0:
                write_last_run(datetime.now())
                print('Pipeline finished successfully; updated last-run timestamp.')
                if notify:
                    _send_telegram_message('✅ Automatische Optimierung ist abgeschlossen.')
            else:
                print(f'Pipeline exited with return code {rc}')
                if notify:
                    _send_telegram_message(f'❌ Automatische Optimierung ist mit Fehlercode {rc} beendet.')
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
