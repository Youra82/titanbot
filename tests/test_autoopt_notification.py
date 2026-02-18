import os
import sys
import json
import runpy
import time
from unittest.mock import Mock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

import subprocess


def _write_cache(path, name, content=''):
    d = os.path.join(PROJECT_ROOT, 'data', 'cache')
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(content)
    return p


def _remove_cache(name):
    p = os.path.join(PROJECT_ROOT, 'data', 'cache', name)
    try:
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        pass


def test_masterrunner_sends_fallback_start_notify_when_scheduler_inprogress(monkeypatch, tmp_path):
    """End-to-end check: when scheduler has '.optimization_in_progress' but
    no '.optimization_start_notified', MasterRunner should send a fallback
    START Telegram and write the start sentinel.

    This test mocks network/process calls (no external side effects).
    """
    # prepare cache: ensure in-progress marker present and remove start-sentinel
    _write_cache(PROJECT_ROOT, '.optimization_in_progress', '2026-02-18T00:00:00')
    _remove_cache('.optimization_start_notified')
    _remove_cache('.master_runner_start_notify_fallback')
    _remove_cache('.master_runner_optimization_inprog_notified')

    # spy/mock send_message so no real Telegram call is made
    calls = []

    def fake_send_message(bot, chat, message):
        calls.append({'bot': bot, 'chat': chat, 'message': message})
        return True

    monkeypatch.setattr('titanbot.utils.telegram.send_message', fake_send_message)

    # prevent subprocess.Popen from actually launching bots/scheduler
    class DummyPopen:
        def __init__(self, *args, **kwargs):
            self.pid = 99999
        def poll(self):
            return None
        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(subprocess, 'Popen', DummyPopen)

    # Run master_runner (it will execute the AUTO-OPTIMIZER status branch)
    runpy.run_path(os.path.join(PROJECT_ROOT, 'master_runner.py'), run_name='__main__')

    # Assertions: send_message was called at least once and fallback sentinel exists
    assert calls, 'MasterRunner did not attempt to send fallback START Telegram'

    fallback_path = os.path.join(PROJECT_ROOT, 'data', 'cache', '.master_runner_start_notify_fallback')
    assert os.path.exists(fallback_path), '.master_runner_start_notify_fallback was not written'

    # cleanup
    _remove_cache('.optimization_in_progress')
    _remove_cache('.optimization_start_notified')
    _remove_cache('.master_runner_start_notify_fallback')
    _remove_cache('.master_runner_optimization_inprog_notified')
