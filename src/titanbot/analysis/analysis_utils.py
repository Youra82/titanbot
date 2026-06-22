# src/titanbot/analysis/analysis_utils.py
"""Shared utilities for titanbot analysis scripts."""

import os
import sys
import json
import glob as _glob
import contextlib
import io
from datetime import datetime, timedelta, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

# ANSI colour constants
GREEN  = '\033[92m'
YELLOW = '\033[93m'
RED    = '\033[91m'
CYAN   = '\033[96m'
NC     = '\033[0m'

CONFIGS_DIR = os.path.join(PROJECT_ROOT, 'src', 'titanbot', 'strategy', 'configs')
SETTINGS_FILE = os.path.join(PROJECT_ROOT, 'settings.json')


def get_settings():
    """Load and return settings.json as a dict."""
    with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_date_range(lookback_weeks=None):
    """Return (start_date, end_date, warmup_date) as ISO strings (UTC).

    start_date  = end_date - (backtest_lookback_weeks + warmup_weeks)
    warmup_date = end_date - backtest_lookback_weeks
    end_date    = today UTC
    """
    settings = get_settings()
    opt = settings.get('optimization_settings', {})

    lw = lookback_weeks if lookback_weeks is not None else opt.get('backtest_lookback_weeks', 2)
    ww = opt.get('warmup_weeks', 4)

    now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt    = now
    warmup_dt = end_dt   - timedelta(weeks=lw)
    start_dt  = warmup_dt - timedelta(weeks=ww)

    return (
        start_dt.strftime('%Y-%m-%d'),
        end_dt.strftime('%Y-%m-%d'),
        warmup_dt.strftime('%Y-%m-%d'),
    )


def load_all_configs():
    """Return a list of config dicts loaded from configs/ directory."""
    pattern = os.path.join(CONFIGS_DIR, 'config_*.json')
    configs = []
    for path in sorted(_glob.glob(pattern)):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            cfg['_config_path'] = path
            configs.append(cfg)
        except Exception as e:
            print(f"{YELLOW}WARNUNG: Konnte {path} nicht laden: {e}{NC}")
    return configs


@contextlib.contextmanager
def _quiet():
    """Suppress stdout/stderr — used to silence download/cache messages."""
    with contextlib.redirect_stdout(io.StringIO()), \
         contextlib.redirect_stderr(io.StringIO()):
        yield


def run_backtest_for_config(cfg, start_date, end_date, start_capital, warmup_date=None, silent=True):
    """Run load_data + run_smc_backtest for a single config dict.

    Returns (result_dict, label_str) or None on error.
    label_str example: 'BTC/USDT:USDT 4h'
    silent=True suppresses all download/cache output.
    """
    try:
        from titanbot.analysis.backtester import load_data, run_smc_backtest

        market   = cfg.get('market', {})
        symbol   = market.get('symbol', '')
        tf       = market.get('timeframe', '')
        smc_p    = dict(cfg.get('strategy', {}))
        risk_p   = dict(cfg.get('risk', {}))

        if not symbol or not tf:
            return None

        smc_p['_timeframe'] = tf
        label = f"{symbol} {tf}"

        ctx = _quiet() if silent else contextlib.nullcontext()
        with ctx:
            data = load_data(symbol, tf, start_date, end_date)

        if data is None or data.empty:
            return None

        with (_quiet() if silent else contextlib.nullcontext()):
            result = run_smc_backtest(
                data, smc_p, risk_p,
                start_capital=start_capital,
                verbose=False,
                backtest_start_date=warmup_date,
            )
        return result, label
    except Exception as e:
        sym = cfg.get('market', {}).get('symbol', '?')
        tf  = cfg.get('market', {}).get('timeframe', '?')
        print(f"{RED}  Fehler bei {sym} {tf}: {e}{NC}")
        return None


def send_chart_telegram(fig, caption, settings=None):
    """Try to send a matplotlib figure via Telegram. Silently ignore errors."""
    try:
        import io
        if settings is None:
            settings = get_settings()
        secret_path = os.path.join(PROJECT_ROOT, 'secret.json')
        with open(secret_path, 'r', encoding='utf-8') as f:
            secret = json.load(f)
        titan_keys = secret.get('titanbot', [{}])
        if isinstance(titan_keys, list):
            keys = titan_keys[0] if titan_keys else {}
        else:
            keys = titan_keys
        bot_token = keys.get('telegram_bot_token', '')
        chat_id   = keys.get('telegram_chat_id', '')
        if not bot_token or not chat_id:
            return

        from titanbot.utils import telegram as tg
        import tempfile

        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=100, bbox_inches='tight')
        buf.seek(0)

        # Write to temp file then send via send_photo (takes file path)
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            tmp.write(buf.getvalue())
            tmp_path = tmp.name

        try:
            tg.send_photo(bot_token, chat_id, tmp_path, caption=caption)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    except Exception:
        pass
